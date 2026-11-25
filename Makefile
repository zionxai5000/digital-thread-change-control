PY ?= python3

demo:      ; $(PY) -m dthread demo all
serve:     ; $(PY) -m dthread serve --open
site:      ; $(PY) -m dthread build-site --out docs
test:      ; $(PY) -m pytest -q
