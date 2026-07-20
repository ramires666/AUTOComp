# Security

AUTOComp is designed to edit copies of industrial-controller projects while
remaining offline from PLC hardware. Security issues can therefore affect both
confidential project text and the integrity of automation logic.

## Reporting

Do not disclose a suspected vulnerability together with a real KV STUDIO
project, credentials, network addresses, screenshots, or controller data in a
public issue. Report it privately to the repository owner and include a minimal,
sanitized reproduction whenever possible.

## Safety boundaries

- Never test against the only copy of a project.
- Never connect AUTOComp to a PLC or enable transfer/monitor/online-edit actions.
- Keep the worker bound to loopback and access it through a trusted tunnel.
- Keep API keys and worker tokens in environment variables.
- Do not commit `config.local.json`, runtime reports, screenshots, or KV project
  files.
- Treat operator-visible strings separately from protocol commands and external
  integration keys.

The current UI worker exposes read-only inventory only. Mutation support must not
be enabled until the apply-gate procedure in `docs/kvstudio-11.62-pilot.md` has
been completed on a disposable project copy.
