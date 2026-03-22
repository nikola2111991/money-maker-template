PLAYBOOK ?= playbooks/tiler-au.json
CITIES ?= all
TARGET ?= 9999

.PHONY: scrape stats due

scrape:
	python3 scraper.py --playbook $(PLAYBOOK) --cities $(CITIES) --target $(TARGET) --resume

stats:
	python3 pipeline.py stats

due:
	python3 pipeline.py due
