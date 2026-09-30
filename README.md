# PISTOL GENESIS AI

Level 1 infrastructure prototype: a lightweight scheduler and read-only inventory loader for explicitly configured servers.

## Requirements and quick start

Python 3.10 or newer. No third-party dependencies.

```bash
python genesis_core.py
python -m unittest discover -s tests -v
```

The demonstration runs a local CPU operation. Registered memory is an accounting budget, not an actual hardware allocation. A selected server name does not cause remote execution: the supplied callback executes locally.

## Three levels

1. Core: scheduling, capacity reservations, inventory snapshots, and draining.
2. Workers: future implementation of authenticated remote execution and capacity reporting.
3. Algorithms and models: future adapters with explicit resource requirements.

## Inventory

Copy `config.example.json` to `config.local.json` and replace the `010101` placeholders. The example is disabled and allocates zero task slots. No connection is made by the demonstration.

`inventory.py` reads explicitly supplied HTTPS endpoints or local inventory files. HTTPS redirects are disabled; certificate verification remains enabled. Inventory timestamps must be timezone-aware and no older than the configured maximum age. Enabled entries containing placeholders are rejected.

Inventory imports create a new scheduler snapshot before accepting tasks. Do not replace an active scheduler with a new snapshot: this would discard reservations. Multiple schedulers do not coordinate reservations.

## Behaviour and limitations

- Thread-safe capacity reservations within one process.
- Resource release when callbacks complete or raise an exception.
- Draining stops new assignments and lets existing callbacks finish.
- No queue: exhausted capacity raises `NoCapacity`.
- Callback memory requirements are supplied by the caller and are not measured or enforced.
- The callback must return only when its work finishes; it must not leave background work using the reservation.
- No remote workers, Remote Desktop automation, GPU discovery, model loading, autoscaling, automatic replication, or persistent task recovery.
- No passwords, private training materials, model weights, or server credentials belong in this repository.

## Validation

A standard-library unittest suite is included for scheduling, capacity exhaustion, failure cleanup, draining, concurrent reservations, and inventory validation. Tests were not executed in the authoring environment; run the command above before deployment.
