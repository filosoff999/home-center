.PHONY: test security contracts build ci

PYTHON ?= python3

test:
	PYTHONPATH=product/control-plane/src $(PYTHON) -m unittest discover -s tests -p 'test_*.py' -v

security:
	$(PYTHON) tests/security_gate.py
	$(PYTHON) tests/security_gate_080.py
	$(PYTHON) tests/security_gate_090.py

contracts:
	$(PYTHON) -m json.tool deploy/profiles/hm-dm-two-node.v1.json >/dev/null
	find contracts -type f -name '*.json' -print0 | sort -z | xargs -0 -n1 $(PYTHON) -m json.tool >/dev/null

build:
	bash deploy/scripts/build-artifact.sh dist

ci: contracts test security build
