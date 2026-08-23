# mikoshi/hercules — TestZeus Hercules with one upstream bug fixed.
#
# Upstream: testzeus/hercules (AGPL-3.0). We run it locally against our own
# products; nothing built from it is distributed or network-served, so the
# AGPL's source-offer obligation is not triggered. If that ever changes, this
# directory IS the complete corresponding source of our modification.
#
# The single change is in patch_hercules.py and is guarded: if upstream moves
# the line it edits, THE BUILD FAILS rather than quietly producing an
# unpatched image.
#
#   docker build -t mikoshi/hercules 05-Orchestrator/qa/engine
FROM testzeus/hercules:latest
COPY patch_hercules.py /tmp/patch_hercules.py
RUN python3 /tmp/patch_hercules.py && rm /tmp/patch_hercules.py
