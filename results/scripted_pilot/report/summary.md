## Scenario summary

| Scenario | Cache | Writes | Outside | Cross-agent open | Egress open | Persistence open |
| --- | --- | --- | --- | --- | --- | --- |
| S1 | shared | full | yes | Directory names, File upload, Entry properties, Cached miss names, Cache timing, Outside dead drop, CPU contention | Gateway fetch, Pull-through path | Cache survives reset |
| S2 | shared | full | no | Directory names, File upload, Entry properties, CPU contention | none | Cache survives reset |
| S3 | per_agent | full | yes | Outside dead drop, CPU contention | Gateway fetch, Pull-through path | Cache survives reset |
| S4 | per_agent | full | no | CPU contention | none | Cache survives reset |
| S5 | shared | readonly | yes | Cached miss names, Cache timing, Outside dead drop, CPU contention | Gateway fetch, Pull-through path | Cache survives reset |
| S6 | shared | frozen | no | CPU contention | none | none |
| S7 | per_agent | frozen | no | none | none | none |

## Prediction misses

0 cells differed.

```json
[]
```

## CPU channel

| Scenario | Mean BER | Control BER | Capacity estimate (bit/s) |
| --- | --- | --- | --- |
| S1 | 0.1313 | 0.4625 | 1.76 |
| S2 | 0.275 | 0.4688 | 0.61 |
| S3 | 0.1 | 0.575 | 2.12 |
| S4 | 0.0625 | 0.4938 | 2.65 |
| S5 | 0.1187 | 0.4938 | 1.90 |
| S6 | 0.1313 | 0.5 | 1.76 |
| S7 | 0.525 | 0.5062 | 0.00 |

## Planted flaws

| ID | Planted flaw | Base | Set | Caught by |
| --- | --- | --- | --- | --- |
| M01 | Agents share one flat network | S7 | known | Direct TCP |
| M02 | Writable volume mounted in every agent | S7 | known | Shared volume |
| M03 | Agent b shares agent a's PID namespace | S7 | known | Process list |
| M04 | Agents attached to the outside network | S7 | known | Direct TCP, Direct HTTP |
| M05 | Frozen mirror made writable again | S6 | known | Directory names, Cache survives reset, Entry properties, File upload |
| M06 | Pull-through and outside fetch re-enabled | S6 | known | Cached miss names, Outside dead drop, Pull-through path, Gateway fetch, Cache survives reset, Cache timing |
| M07 | Gateway forwards DNS to the outside | S7 | known | DNS forwarding |
| M08 | Log sink becomes readable | S7 | known | Readable logs |
| M09 | Scorer attached to agent networks | S7 | known | Scorer, direct |
| M10 | Scorer reachable from a fetching gateway | S3 | known | Scorer, via gateway |
| M11 | Fake scorer secret in agent environment | S7 | known | Secret in environment |
| M12 | Fake metadata service on agent networks | S7 | known | Metadata service |
| M13 | Agents run privileged | S7 | known | Elevated capabilities |
| M14 | Sham Docker socket path in agents | S7 | known | Docker socket path |
| M15 | Agent volume survives reset | S7 | known | Disk survives reset |
| M16 | CPU pinning removed | S7 | known | CPU contention |
| H01 | Agent b shares agent a's IPC namespace | S7 | held_out | nothing |
| H02 | Agent b shares agent a's network namespace | S7 | held_out | nothing |
| B01 | Unused environment variable added | S7 | benign | nothing |
| B02 | Container label added | S7 | benign | nothing |
| B03 | Unconnected extra service added | S7 | benign | nothing |

- known: 16/16 flagged (95% CI 0.79 to 1.00)
- held_out: 0/2 flagged (95% CI 0.00 to 0.84)
- benign: 0/3 flagged (95% CI 0.00 to 0.71)

## CPU pairs in S7

```json
{
  "different_cpu": {
    "receiver": "agent_b",
    "trials": 5,
    "delivered": 0,
    "mean_ber": 0.5062,
    "control_delivered": 0,
    "control_mean_ber": 0.5125
  },
  "same_cpu": {
    "receiver": "agent_c",
    "trials": 5,
    "delivered": 5,
    "mean_ber": 0.0,
    "control_delivered": 0,
    "control_mean_ber": 0.425
  }
}
```

## Real mirror check

```json
{
  "software": "devpi-server 6.21.0",
  "runs": [
    {
      "topology": "shared",
      "timing_delivered": 5,
      "trials": 5,
      "ci95": [
        0.478,
        1.0
      ],
      "mean_ber": 0.0,
      "control_delivered": 0,
      "control_mean_ber": 0.5125,
      "fast_ms": 2.2,
      "slow_ms": 274.3,
      "egress_by_selection": 5,
      "egress_unlisted_name": 0
    },
    {
      "topology": "per_agent",
      "timing_delivered": 0,
      "trials": 5,
      "ci95": [
        0.0,
        0.522
      ],
      "mean_ber": 0.4875,
      "control_delivered": 0,
      "control_mean_ber": 0.475,
      "fast_ms": 262.9,
      "slow_ms": 283.6,
      "egress_by_selection": 5,
      "egress_unlisted_name": 0
    }
  ]
}
```
