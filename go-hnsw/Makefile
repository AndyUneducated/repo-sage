.PHONY: build test lint bench fmt clean

GO        ?= go
BIN_DIR   := bin

build:
	@mkdir -p $(BIN_DIR)
	$(GO) build -o $(BIN_DIR)/hnsw-server ./cmd/server
	$(GO) build -o $(BIN_DIR)/hnsw-bench ./cmd/bench

test:
	$(GO) test -race -count=1 ./...

lint:
	@if [ -n "$$(gofmt -l . 2>&1 | tee /dev/stderr)" ]; then echo 'gofmt issues'; exit 1; fi
	$(GO) vet ./...

fmt:
	gofmt -w .

bench: build
	$(BIN_DIR)/hnsw-bench --synthetic 20000 --M 16 --efC 200 --ef 16,64,128,256 \
		--snapshot /tmp/hnsw-bench.hnsw --header

clean:
	rm -rf $(BIN_DIR)
