# KV STUDIO 11.62 pilot

The pilot determines the real accessibility selectors and the exact content of a
mnemonic export before mutation support is enabled.

## Machine preparation

1. Use the free physical Windows computer and a dedicated local account.
2. Keep the display at a fixed resolution and 100% scaling.
3. Disable display sleep for the duration of the run; use a physical display or
   HDMI dummy adapter if necessary.
4. Launch the worker in that interactive account, not as a Windows service.
5. Keep KV STUDIO in Editor/offline mode. Disconnect or firewall the PLC network.
6. Copy the complete project directory and open only the copy.

## Baseline capture

1. Record the KV STUDIO About dialog showing version 11.62.
2. Run `autocomp doctor` and `autocomp inventory-ui`.
3. Save the baseline `Check/Compile` result.
4. Export the mnemonic list for `PartsLife` using the Chinese KV STUDIO menu:
   `文件 → 助记符列表 → 保存` (wording may differ slightly in this build).
5. Confirm that the export includes:
   - the blue `寿命设置` heading;
   - the grey `石墨盘暂定即将寿命90次` line;
   - logic containing constants such as `#90` and `#100`.
6. Hash the whole project copy and store the export with checkpoint `00_original_cn`.

## Apply-gate criteria

Mutation code remains disabled until all of the following are demonstrated on a
throwaway project copy:

- KV STUDIO's process and primary window are identified reliably.
- The project tree and ladder editor controls have stable selectors, or a bounded
  visual fallback is documented.
- A single test heading can be changed and then reverted.
- Save As targets a new directory/file.
- The mnemonic comparison detects changes to instructions, device addresses, and
  `#` numeric constants while ignoring comment translation.
- Compile diagnostics match the baseline.

## US/Global validation

Prefer US KV STUDIO 11.62 rather than upgrading to version 12. Do not remove the
Chinese installation. If side-by-side installation is unavailable, use another
machine or a VM/snapshot.

Open only checkpoint `05_full_english_cn_verified`, immediately Save As to a new
copy, and compare program/module counts, mnemonic logic, device comments, and
compile diagnostics. If non-ASCII content causes an error or mojibake, return to
Chinese 11.62, convert remaining Russian/Chinese project text to English, and
repeat the test.
