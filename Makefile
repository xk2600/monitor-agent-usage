.PHONY: run sync status test

run:
	python3 -m monitor_agent_usage serve --port 8420

sync:
	python3 -m monitor_agent_usage sync

status:
	python3 -m monitor_agent_usage status --hours 24

test:
	python3 -m unittest discover -s tests -v
