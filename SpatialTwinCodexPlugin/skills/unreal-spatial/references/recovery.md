# Interrupted execution and saving

Read when a patch fails, conflicts, loses a reply or has uncertain saving.

Use `patch_status(patch_id, recover=true)` to reconcile recorded effects with
Canonical without editor calls. It never replays writes or saves packages;
recovered APPLIED describes the returned revision. `saved=true` additionally
requires a durable native save receipt, matching scope/files and clean state;
otherwise saving remains unconfirmed. Never infer saved from MCP OK alone.
Unknown creation identities or mismatched effects stay unresolved. Do not
guess IDs by label/position or spawn replacements. Recovery requires an idle
executor; keep original failure receipts and verify saving separately.
Native `patch_result_journal=1` preserves terminal batch references before
the reply, enabling recovery after lost creation responses. A crash before
that journal write remains uncertain; never replay the batch to find out.
For a terminated partial batch, `patch_continue` prepares only the unexecuted
suffix after proving the completed and uncertain operation effects. Inspect
its compact validation, then apply its returned patch ID normally. It refuses
unknown effects/identities; never bypass that refusal. Recover the parent after
the child is confirmed. Parent package saving is a separate proof.
For a recorded terminal partial package save, use the explicit action CLI
`apply_patch.py --root <Twin> --patch <id> --finalize-save`. It verifies the
existing effects and saves only remaining owned packages; it never replays
Actor operations. Native capability `patch_partial_save=1` is required.
A changed/unloaded package, different editor session or uncertain outcome
refuses continuation. Preserve that refusal; do not force-save the world.
A completed native journal needs only verification, not another write.
