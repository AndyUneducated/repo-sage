package hnsw

import (
	"bufio"
	"encoding/binary"
	"fmt"
	"math"
	"math/rand"
	"os"
)

// On-disk format (go-hnsw v2). All integers little-endian.
//
//	header   (64 B): magic "HNSW" | version u16 | metric u8 | _pad u8
//	                 | dim u32 | M u32 | maxM u32 | efC u32 | efS u32
//	                 | maxLevel u32 | entry u32 | _pad u32
//	                 | n u64 | levelMult f64 | seed i64
//	idOff    : (n+1) u64  byte offsets into idData
//	idData   : packed id bytes
//	levels   : n u16      top layer of each node
//	off0     : (n+1) u64  layer-0 CSR row offsets (in u32 units)
//	offU     : (n+1) u64  upper-layer blob CSR row offsets (in u32 units)
//	PAD      : to 64 B
//	adj0     : off0[n] u32  layer-0 neighbours (contiguous, cache-friendly)
//	adjU     : offU[n] u32  upper-layer blob: per node [count u32, ids...] for lc=1..L
//	PAD      : to 64 B
//	vectors  : n*dim f32  contiguous arena (the mmap lazy-paging target)
//
// We choose CSR over per-node slices because mmap loads it as a single span
// and sequential layer-0 scans (the search hot path) get cache locality.
// Recover mmaps the file and aliases the large arrays (adj0/adjU/vectors)
// straight out of the mapping; only the small offset/level arrays are copied.
// Snapshot writes a fresh file atomically (tmp + fsync + rename).
const (
	snapMagic   = "HNSW"
	snapVersion = uint16(2)
	headerSize  = 64
	snapAlign   = 64
)

// renameHook is the commit step, indirected so tests can simulate a failed
// rename and assert the previous snapshot survives intact.
var renameHook = os.Rename

// Snapshot writes the index to `path` atomically: it streams a complete image
// to `path + ".tmp"`, fsyncs it, then renames over `path`. A crash at any point
// leaves the previous snapshot (if any) untouched and the .tmp as a stale
// orphan that the next Snapshot overwrites.
func (ix *Index) Snapshot(path string) error {
	ix.mu.RLock()
	defer ix.mu.RUnlock()
	if ix.closed {
		return errClosed
	}
	tmp, err := ix.writeSnapshotTmp(path)
	if err != nil {
		if tmp != "" {
			_ = os.Remove(tmp)
		}
		return err
	}
	if err := renameHook(tmp, path); err != nil {
		_ = os.Remove(tmp)
		return fmt.Errorf("hnsw: commit snapshot: %w", err)
	}
	return nil
}

// writeSnapshotTmp writes the full image to the tmp path and returns it. It is
// split out from Snapshot so tests can verify the tmp content is a valid,
// independent snapshot before the rename commit.
func (ix *Index) writeSnapshotTmp(path string) (string, error) {
	g := ix.graph
	cfg := ix.cfg
	n := len(g.nodes)
	dim := cfg.Dim
	tmp := path + ".tmp"

	f, err := os.OpenFile(tmp, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o644)
	if err != nil {
		return "", fmt.Errorf("hnsw: create snapshot tmp: %w", err)
	}
	defer f.Close()
	w := bufio.NewWriterSize(f, 1<<20)

	var hdr [headerSize]byte
	copy(hdr[0:4], snapMagic)
	binary.LittleEndian.PutUint16(hdr[4:6], snapVersion)
	hdr[6] = byte(cfg.Metric)
	binary.LittleEndian.PutUint32(hdr[8:12], uint32(dim))
	binary.LittleEndian.PutUint32(hdr[12:16], uint32(cfg.M))
	binary.LittleEndian.PutUint32(hdr[16:20], uint32(cfg.MaxM))
	binary.LittleEndian.PutUint32(hdr[20:24], uint32(cfg.EfConstruction))
	binary.LittleEndian.PutUint32(hdr[24:28], uint32(cfg.EfSearch))
	binary.LittleEndian.PutUint32(hdr[28:32], uint32(g.maxLvl))
	binary.LittleEndian.PutUint32(hdr[32:36], g.entry)
	binary.LittleEndian.PutUint64(hdr[40:48], uint64(n))
	binary.LittleEndian.PutUint64(hdr[48:56], math.Float64bits(g.levelMult))
	binary.LittleEndian.PutUint64(hdr[56:64], uint64(cfg.Seed))
	if _, err := w.Write(hdr[:]); err != nil {
		return tmp, err
	}
	pos := int64(headerSize)

	scratch := make([]byte, 8)
	writeU64 := func(v uint64) error {
		binary.LittleEndian.PutUint64(scratch, v)
		_, e := w.Write(scratch)
		return e
	}

	// idOff (n+1) + idData
	for _, off := range g.idOff {
		if err := writeU64(off); err != nil {
			return tmp, err
		}
	}
	pos += int64(len(g.idOff)) * 8
	if _, err := w.Write(g.idData); err != nil {
		return tmp, err
	}
	pos += int64(len(g.idData))

	// levels (n u16)
	s2 := make([]byte, 2)
	for i := 0; i < n; i++ {
		binary.LittleEndian.PutUint16(s2, uint16(len(g.nodes[i].neighbors)-1))
		if _, err := w.Write(s2); err != nil {
			return tmp, err
		}
	}
	pos += int64(n) * 2

	// off0 (n+1) prefix sums of len(neighbors[0])
	var adj0Len uint64
	if err := writeU64(0); err != nil {
		return tmp, err
	}
	for i := 0; i < n; i++ {
		adj0Len += uint64(len(g.nodes[i].neighbors[0]))
		if err := writeU64(adj0Len); err != nil {
			return tmp, err
		}
	}
	pos += int64(n+1) * 8

	// offU (n+1) prefix sums of the upper-layer blob length
	var adjULen uint64
	if err := writeU64(0); err != nil {
		return tmp, err
	}
	for i := 0; i < n; i++ {
		L := len(g.nodes[i].neighbors) - 1
		for lc := 1; lc <= L; lc++ {
			adjULen += 1 + uint64(len(g.nodes[i].neighbors[lc]))
		}
		if err := writeU64(adjULen); err != nil {
			return tmp, err
		}
	}
	pos += int64(n+1) * 8

	if pos, err = padTo(w, pos, snapAlign); err != nil {
		return tmp, err
	}

	// adj0
	for i := 0; i < n; i++ {
		if b := uint32SliceToBytes(g.nodes[i].neighbors[0]); b != nil {
			if _, err := w.Write(b); err != nil {
				return tmp, err
			}
		}
	}
	pos += int64(adj0Len) * 4

	// adjU: per node, per upper layer: count then ids
	cnt := make([]byte, 4)
	for i := 0; i < n; i++ {
		L := len(g.nodes[i].neighbors) - 1
		for lc := 1; lc <= L; lc++ {
			nb := g.nodes[i].neighbors[lc]
			binary.LittleEndian.PutUint32(cnt, uint32(len(nb)))
			if _, err := w.Write(cnt); err != nil {
				return tmp, err
			}
			if b := uint32SliceToBytes(nb); b != nil {
				if _, err := w.Write(b); err != nil {
					return tmp, err
				}
			}
		}
	}
	pos += int64(adjULen) * 4

	if pos, err = padTo(w, pos, snapAlign); err != nil {
		return tmp, err
	}

	// vectors
	for i := 0; i < n; i++ {
		if _, err := w.Write(float32SliceToBytes(g.nodes[i].vector)); err != nil {
			return tmp, err
		}
	}

	if err := w.Flush(); err != nil {
		return tmp, err
	}
	if err := f.Sync(); err != nil {
		return tmp, err
	}
	return tmp, nil
}

// Recover loads an index from disk. The vector arena and the CSR adjacency are
// mmap'd and aliased zero-copy; the small offset/level arrays are parsed
// eagerly. The returned index is frozen (read-only mmap) until the first Add
// thaws it. Call Close to unmap.
func Recover(path string) (*Index, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("hnsw: open snapshot: %w", err)
	}
	st, err := f.Stat()
	if err != nil {
		_ = f.Close()
		return nil, err
	}
	size := int(st.Size())
	if size < headerSize {
		_ = f.Close()
		return nil, errBadSnapshot("file smaller than header")
	}
	data, err := mapFile(f, size)
	_ = f.Close() // the mapping outlives the fd
	if err != nil {
		return nil, fmt.Errorf("hnsw: mmap snapshot: %w", err)
	}
	ix, err := recoverFromBytes(data)
	if err != nil {
		_ = munmap(data)
		return nil, err
	}
	ix.mmap = data
	return ix, nil
}

func recoverFromBytes(data []byte) (*Index, error) {
	if len(data) < headerSize {
		return nil, errBadSnapshot("truncated header")
	}
	if string(data[0:4]) != snapMagic {
		return nil, errBadSnapshot("bad magic")
	}
	if v := binary.LittleEndian.Uint16(data[4:6]); v != snapVersion {
		return nil, fmt.Errorf("hnsw: unsupported snapshot version %d (want %d)", v, snapVersion)
	}
	metric := Metric(data[6])
	dim := int(binary.LittleEndian.Uint32(data[8:12]))
	cfg := Config{
		Dim:            dim,
		M:              int(binary.LittleEndian.Uint32(data[12:16])),
		MaxM:           int(binary.LittleEndian.Uint32(data[16:20])),
		EfConstruction: int(binary.LittleEndian.Uint32(data[20:24])),
		EfSearch:       int(binary.LittleEndian.Uint32(data[24:28])),
		Metric:         metric,
		Distance:       metric.Func(),
		Heuristic:      true,
		Seed:           int64(binary.LittleEndian.Uint64(data[56:64])),
	}
	maxLvl := int(binary.LittleEndian.Uint32(data[28:32]))
	entry := binary.LittleEndian.Uint32(data[32:36])
	n64 := binary.LittleEndian.Uint64(data[40:48])
	levelMult := math.Float64frombits(binary.LittleEndian.Uint64(data[48:56]))
	cfg.LevelMult = levelMult
	if dim <= 0 {
		return nil, errBadSnapshot("non-positive dim")
	}
	if n64 > uint64(len(data)) { // n can't exceed the byte count
		return nil, errBadSnapshot("implausible node count")
	}
	n := int(n64)

	pos := headerSize
	idOff, pos, err := readU64Slice(data, pos, n+1)
	if err != nil {
		return nil, err
	}
	idData, pos, err := readBytes(data, pos, int(idOff[n]))
	if err != nil {
		return nil, err
	}
	levels, pos, err := readU16Slice(data, pos, n)
	if err != nil {
		return nil, err
	}
	off0, pos, err := readU64Slice(data, pos, n+1)
	if err != nil {
		return nil, err
	}
	offU, pos, err := readU64Slice(data, pos, n+1)
	if err != nil {
		return nil, err
	}
	pos = alignUp(pos, snapAlign)
	adj0, pos, err := aliasU32(data, pos, int(off0[n]))
	if err != nil {
		return nil, err
	}
	adjU, pos, err := aliasU32(data, pos, int(offU[n]))
	if err != nil {
		return nil, err
	}
	pos = alignUp(pos, snapAlign)
	arena, _, err := aliasF32(data, pos, n*dim)
	if err != nil {
		return nil, err
	}

	nodes := make([]node, n)
	totalLayers := n
	for i := 0; i < n; i++ {
		totalLayers += int(levels[i])
	}
	flat := make([][]uint32, totalLayers)
	cursor := 0
	for i := 0; i < n; i++ {
		L := int(levels[i])
		nb := flat[cursor : cursor+L+1]
		cursor += L + 1

		a, b := off0[i], off0[i+1]
		if a > b || b > uint64(len(adj0)) {
			return nil, errBadSnapshot("adj0 row out of range")
		}
		nb[0] = adj0[a:b:b]

		ua, ub := offU[i], offU[i+1]
		if ua > ub || ub > uint64(len(adjU)) {
			return nil, errBadSnapshot("adjU row out of range")
		}
		blob := adjU[ua:ub]
		bpos := 0
		for lc := 1; lc <= L; lc++ {
			if bpos >= len(blob) {
				return nil, errBadSnapshot("adjU blob underrun")
			}
			c := int(blob[bpos])
			bpos++
			if bpos+c > len(blob) {
				return nil, errBadSnapshot("adjU blob overrun")
			}
			nb[lc] = blob[bpos : bpos+c : bpos+c]
			bpos += c
		}

		vs := i * dim
		nodes[i] = node{vector: arena[vs : vs+dim : vs+dim], neighbors: nb}
	}

	seed := cfg.Seed
	if seed == 0 {
		seed = 1337
	}
	g := &graph{
		cfg:       cfg,
		nodes:     nodes,
		idData:    idData,
		idOff:     idOff,
		idIndex:   nil, // built lazily on first Add (DD-026)
		entry:     entry,
		maxLvl:    maxLvl,
		hasAny:    n > 0,
		frozen:    true,
		rng:       rand.New(rand.NewSource(seed)),
		levelMult: levelMult,
	}
	return &Index{cfg: cfg, graph: g}, nil
}

func padTo(w *bufio.Writer, pos int64, align int64) (int64, error) {
	rem := pos % align
	if rem == 0 {
		return pos, nil
	}
	var zero [snapAlign]byte
	pad := align - rem
	if _, err := w.Write(zero[:pad]); err != nil {
		return pos, err
	}
	return pos + pad, nil
}

func alignUp(pos, align int) int {
	if r := pos % align; r != 0 {
		return pos + (align - r)
	}
	return pos
}

func readU64Slice(data []byte, pos, count int) ([]uint64, int, error) {
	end := pos + count*8
	if count < 0 || end < pos || end > len(data) {
		return nil, pos, errBadSnapshot("u64 section out of range")
	}
	out := make([]uint64, count)
	for i := 0; i < count; i++ {
		out[i] = binary.LittleEndian.Uint64(data[pos+i*8:])
	}
	return out, end, nil
}

func readU16Slice(data []byte, pos, count int) ([]uint16, int, error) {
	end := pos + count*2
	if count < 0 || end < pos || end > len(data) {
		return nil, pos, errBadSnapshot("u16 section out of range")
	}
	out := make([]uint16, count)
	for i := 0; i < count; i++ {
		out[i] = binary.LittleEndian.Uint16(data[pos+i*2:])
	}
	return out, end, nil
}

func readBytes(data []byte, pos, count int) ([]byte, int, error) {
	end := pos + count
	if count < 0 || end < pos || end > len(data) {
		return nil, pos, errBadSnapshot("bytes section out of range")
	}
	return data[pos:end:end], end, nil
}

func aliasU32(data []byte, pos, count int) ([]uint32, int, error) {
	if count == 0 {
		return nil, pos, nil
	}
	end := pos + count*4
	if count < 0 || end < pos || end > len(data) {
		return nil, pos, errBadSnapshot("u32 section out of range")
	}
	if pos%4 != 0 {
		return nil, pos, errBadSnapshot("u32 section misaligned")
	}
	return bytesToUint32(data[pos:end:end]), end, nil
}

func aliasF32(data []byte, pos, count int) ([]float32, int, error) {
	if count == 0 {
		return nil, pos, nil
	}
	end := pos + count*4
	if count < 0 || end < pos || end > len(data) {
		return nil, pos, errBadSnapshot("f32 section out of range")
	}
	if pos%4 != 0 {
		return nil, pos, errBadSnapshot("f32 section misaligned")
	}
	return bytesToFloat32(data[pos:end:end]), end, nil
}

func errBadSnapshot(what string) error {
	return fmt.Errorf("hnsw: corrupt snapshot: %s", what)
}
