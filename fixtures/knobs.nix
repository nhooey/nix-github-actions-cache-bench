# Synthetic workloads: n derivations x size bytes x sleep seconds.
# Each set isolates one cost, so a backend's per-path, per-byte and
# build-time behaviour can be told apart.
# GitHub's ubuntu-latest runner has 4 cores, so Nix builds 4 at a time:
# sleep-bound sets take about ceil(n / 4) x sleep seconds when built.
{
  # Per-path cost: 1,000 paths, 4 MiB in all, no build time.
  # Upload-rate limits (200/min) and per-entry overhead show up here.
  many-small = { n = 1000; size = 4096; sleep = 0; };

  # Per-byte cost: 4 paths, 1 GiB in all, no build time.
  few-large = { n = 4; size = 268435456; sleep = 0; };

  # Build-time saving: 4 paths of 1 MiB, 300 s each (about 5 min built).
  # A hit should cost seconds whatever the backend.
  slow = { n = 4; size = 1048576; sleep = 300; };
}
