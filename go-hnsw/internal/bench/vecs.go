// Package bench holds the SIFT-1M benchmark harness: dataset IO (the TEXMEX
// .fvecs / .ivecs formats), recall computation, and the build/query runner the
// cmd/bench CLI drives. It lives under internal/ so it can be unit-tested
// (a package main cannot).
package bench

import (
	"bufio"
	"encoding/binary"
	"fmt"
	"io"
	"math"
	"os"
)

// ReadFvecs reads a TEXMEX .fvecs file: a sequence of vectors, each laid out as
// an int32 dimension `d` followed by `d` little-endian float32 values. When
// max > 0 it stops after max vectors.
func ReadFvecs(path string, max int) ([][]float32, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	r := bufio.NewReaderSize(f, 1<<20)

	var out [][]float32
	var dimBuf [4]byte
	dim := -1
	for {
		_, err := io.ReadFull(r, dimBuf[:])
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, fmt.Errorf("fvecs %s: read dim: %w", path, err)
		}
		d := int(int32(binary.LittleEndian.Uint32(dimBuf[:])))
		if d <= 0 || d > 1<<20 {
			return nil, fmt.Errorf("fvecs %s: implausible dim %d", path, d)
		}
		if dim == -1 {
			dim = d
		} else if d != dim {
			return nil, fmt.Errorf("fvecs %s: ragged dim %d != %d", path, d, dim)
		}
		raw := make([]byte, d*4)
		if _, err := io.ReadFull(r, raw); err != nil {
			return nil, fmt.Errorf("fvecs %s: read vector: %w", path, err)
		}
		v := make([]float32, d)
		for i := 0; i < d; i++ {
			v[i] = math.Float32frombits(binary.LittleEndian.Uint32(raw[i*4:]))
		}
		out = append(out, v)
		if max > 0 && len(out) >= max {
			break
		}
	}
	return out, nil
}

// ReadIvecs reads a TEXMEX .ivecs file (ground truth): same framing as fvecs
// but the payload is `d` int32 values (neighbour indices).
func ReadIvecs(path string, max int) ([][]int32, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	r := bufio.NewReaderSize(f, 1<<20)

	var out [][]int32
	var dimBuf [4]byte
	dim := -1
	for {
		_, err := io.ReadFull(r, dimBuf[:])
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, fmt.Errorf("ivecs %s: read dim: %w", path, err)
		}
		d := int(int32(binary.LittleEndian.Uint32(dimBuf[:])))
		if d <= 0 || d > 1<<20 {
			return nil, fmt.Errorf("ivecs %s: implausible dim %d", path, d)
		}
		if dim == -1 {
			dim = d
		} else if d != dim {
			return nil, fmt.Errorf("ivecs %s: ragged dim %d != %d", path, d, dim)
		}
		raw := make([]byte, d*4)
		if _, err := io.ReadFull(r, raw); err != nil {
			return nil, fmt.Errorf("ivecs %s: read row: %w", path, err)
		}
		v := make([]int32, d)
		for i := 0; i < d; i++ {
			v[i] = int32(binary.LittleEndian.Uint32(raw[i*4:]))
		}
		out = append(out, v)
		if max > 0 && len(out) >= max {
			break
		}
	}
	return out, nil
}

// WriteFvecs writes vectors in .fvecs format. Used by tests and tools.
func WriteFvecs(path string, vecs [][]float32) error {
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer f.Close()
	w := bufio.NewWriterSize(f, 1<<20)
	var buf [4]byte
	for _, v := range vecs {
		binary.LittleEndian.PutUint32(buf[:], uint32(int32(len(v))))
		if _, err := w.Write(buf[:]); err != nil {
			return err
		}
		for _, x := range v {
			binary.LittleEndian.PutUint32(buf[:], math.Float32bits(x))
			if _, err := w.Write(buf[:]); err != nil {
				return err
			}
		}
	}
	return w.Flush()
}

// WriteIvecs writes rows in .ivecs format. Used by tests.
func WriteIvecs(path string, rows [][]int32) error {
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer f.Close()
	w := bufio.NewWriterSize(f, 1<<20)
	var buf [4]byte
	for _, row := range rows {
		binary.LittleEndian.PutUint32(buf[:], uint32(int32(len(row))))
		if _, err := w.Write(buf[:]); err != nil {
			return err
		}
		for _, x := range row {
			binary.LittleEndian.PutUint32(buf[:], uint32(x))
			if _, err := w.Write(buf[:]); err != nil {
				return err
			}
		}
	}
	return w.Flush()
}
