# Codex: Money Maker template

`README.md` opisuje alat, a `CLAUDE.md` obradu leadova.
Postojeći Python kod i `config.py` određuju stvarne putanje i spoljne efekte.

- Koristi projektno Python okruženje sa `requirements.txt`. Iz korena:
  `python -m pytest test_scoring.py test_research.py test_render.py test_pipeline.py test_scraper.py -q`.
  To je postojeći test paket; ovaj repo nema CI workflow.
- U ovom šablonu nema Makefile-a. Za scraper koristi njegov direktni CLI iz
  README-a, kroz isti interpreter. Mrežni scraper i `render.py --deploy`
  nisu lokalne provere instalacije i ne pokreću se samo radi Codex pripreme.
- `research.py` koristi Claude CLI kao zavisnost aplikacije. Izbor modela tog
  procesa odvojen je od korisnikovog Codex modela.
- `MM_LEADS_DIR` i `MM_DEPLOY_REPO` imaju deljene podrazumevane putanje izvan
  repoa. U paralelnim zadacima zadaj odvojene podatke i izlaze; worktree nije
  izolacija tih direktorijuma. Ne kopiraj prijavne tokene u radno stablo.
