import json
import psycopg2.extras
from db import run_query, run_write, run_write_returning_one, dataset_exists, get_write_conn
from utils import clamp_limit
from hevy_api import push_routine_to_hevy_internal


def register(mcp):

    @mcp.tool()
    def create_program_change_proposal(
        routine_title: str,
        exercise_name: str,
        set_index: int,
        recommendation_type: str,
        proposed_weight_kg: float = None,
        proposed_reps: int = None,
        proposed_set_type: str = "",
        rationale: str = "",
        current_weight_kg: float = None,
        current_reps: int = None,
        current_set_type: str = "",
        supporting_context_json: str = "{}"
    ) -> dict:
        """Create a pending program change proposal. Does NOT modify the routine yet."""
        if not dataset_exists("hevy_program_change_proposals"):
            return {"message": "hevy_program_change_proposals table does not exist yet."}
        try:
            supporting_context = psycopg2.extras.Json(json.loads(supporting_context_json or "{}"))
        except Exception as e:
            return {"message": f"Invalid supporting_context_json: {e}"}

        return run_write_returning_one("""
            insert into hevy_program_change_proposals (
                created_by, status, routine_title, exercise_name, set_index,
                current_weight_kg, current_reps, current_set_type,
                proposed_weight_kg, proposed_reps, proposed_set_type,
                recommendation_type, rationale, supporting_context
            ) values (
                'claude', 'pending', %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s
            ) returning *
        """, (
            routine_title, exercise_name, set_index,
            current_weight_kg, current_reps, current_set_type or None,
            proposed_weight_kg, proposed_reps, proposed_set_type or None,
            recommendation_type, rationale or None, supporting_context,
        ))

    @mcp.tool()
    def get_pending_program_change_proposals(limit: int = 50) -> list:
        """Return pending program change proposals."""
        if not dataset_exists("hevy_program_change_proposals"):
            return [{"message": "hevy_program_change_proposals table does not exist yet."}]
        return run_query("""
            select * from hevy_program_change_proposals
            where status = 'pending' order by created_at desc limit %s
        """, (clamp_limit(limit, 1, 200),))

    @mcp.tool()
    def get_program_change_proposals(status: str = "", limit: int = 100) -> list:
        """Return program change proposals. Optional status filter: pending/approved/rejected/applied."""
        if not dataset_exists("hevy_program_change_proposals"):
            return [{"message": "hevy_program_change_proposals table does not exist yet."}]
        limit = clamp_limit(limit, 1, 500)
        if status:
            return run_query(
                "select * from hevy_program_change_proposals where status = %s order by created_at desc limit %s",
                (status, limit)
            )
        return run_query("select * from hevy_program_change_proposals order by created_at desc limit %s", (limit,))

    @mcp.tool()
    def update_program_change_status(
        proposal_id: str,
        new_status: str,
        reviewed_by: str = "frank"
    ) -> dict:
        """Update a proposal status to approved or rejected."""
        if not dataset_exists("hevy_program_change_proposals"):
            return {"message": "hevy_program_change_proposals table does not exist yet."}
        if new_status not in ("approved", "rejected"):
            return {"message": "new_status must be 'approved' or 'rejected'."}
        return run_write_returning_one("""
            update hevy_program_change_proposals
            set status = %s, reviewed_by = %s, reviewed_at = now()
            where proposal_id = %s::uuid returning *
        """, (new_status, reviewed_by, proposal_id))

    @mcp.tool()
    def apply_program_change_proposal(proposal_id: str) -> dict:
        """Apply one approved proposal to hevy_routine_sets and mark it as applied.
        Updates the LOCAL DB-backed routine tables only."""
        if not dataset_exists("hevy_program_change_proposals"):
            return {"message": "hevy_program_change_proposals table does not exist yet."}

        conn = get_write_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "select * from hevy_program_change_proposals where proposal_id = %s::uuid for update",
                    (proposal_id,)
                )
                proposal = cur.fetchone()
                if not proposal:
                    conn.rollback()
                    return {"message": "Proposal not found."}
                proposal = dict(proposal)
                if proposal["status"] != "approved":
                    conn.rollback()
                    return {"message": f"Proposal status must be 'approved' before apply. Current status: {proposal['status']}"}

                cur.execute("""
                    update hevy_routine_sets rs
                    set
                        weight_kg = coalesce(%s, rs.weight_kg),
                        reps      = coalesce(%s, rs.reps),
                        set_type  = coalesce(nullif(%s, ''), rs.set_type)
                    from hevy_routine_exercises re
                    join hevy_routines r on re.routine_id = r.routine_id
                    where rs.routine_id    = re.routine_id
                      and rs.exercise_index = re.exercise_index
                      and r.title          = %s
                      and re.title         = %s
                      and rs.set_index     = %s
                    returning
                        r.title  as routine_title,
                        re.title as exercise_name,
                        rs.set_index, rs.weight_kg, rs.reps, rs.set_type
                """, (
                    proposal["proposed_weight_kg"],
                    proposal["proposed_reps"],
                    proposal["proposed_set_type"] or "",
                    proposal["routine_title"],
                    proposal["exercise_name"],
                    proposal["set_index"],
                ))
                updated_rows = cur.fetchall()
                if not updated_rows:
                    conn.rollback()
                    return {"message": "No matching routine set row found to update.", "proposal": proposal}

                cur.execute("""
                    update hevy_program_change_proposals
                    set status = 'applied', applied_at = now()
                    where proposal_id = %s::uuid
                    returning proposal_id, status, applied_at
                """, (proposal_id,))
                applied_meta = dict(cur.fetchone())

            conn.commit()
            return {
                "message": "Proposal applied successfully.",
                "applied_proposal": applied_meta,
                "updated_rows": [dict(r) for r in updated_rows]
            }
        except Exception as e:
            conn.rollback()
            return {"message": f"Apply failed: {e}"}
        finally:
            conn.close()

    @mcp.tool()
    def push_routine_to_hevy(routine_title: str) -> dict:
        """Push the current DB-backed routine state to Hevy.
        Useful if a previous Hevy push failed and you want to retry."""
        try:
            result = push_routine_to_hevy_internal(routine_title)
            return {"message": "Routine pushed to Hevy successfully.", **result}
        except Exception as e:
            return {"message": f"Routine push failed: {e}"}

    @mcp.tool()
    def approve_and_apply_program_change_proposal(
        proposal_id: str,
        reviewed_by: str = "frank"
    ) -> dict:
        """Approve one proposal, apply it to the local DB routine tables,
        then push the updated routine to Hevy."""
        if not dataset_exists("hevy_program_change_proposals"):
            return {"message": "hevy_program_change_proposals table does not exist yet."}

        approved = update_program_change_status(proposal_id, "approved", reviewed_by=reviewed_by)
        if approved.get("message") and "proposal_id" not in approved:
            return approved

        applied = apply_program_change_proposal(proposal_id)
        if applied.get("message") != "Proposal applied successfully.":
            return {"message": "Proposal approved but local DB apply failed.",
                    "approval_result": approved, "apply_result": applied}

        updated_rows = applied.get("updated_rows", [])
        if not updated_rows:
            return {"message": "Proposal applied locally, but no updated rows returned.",
                    "approval_result": approved, "apply_result": applied}

        routine_title = updated_rows[0]["routine_title"]
        try:
            push_result = push_routine_to_hevy_internal(routine_title)
            run_write("""
                update hevy_program_change_proposals
                set hevy_push_status = 'success', hevy_pushed_at = now(), hevy_push_error = null
                where proposal_id = %s::uuid
            """, (proposal_id,))
            return {
                "message": "Proposal approved, applied locally, and pushed to Hevy successfully.",
                "approval_result": approved, "apply_result": applied, "hevy_push_result": push_result
            }
        except Exception as e:
            run_write("""
                update hevy_program_change_proposals
                set hevy_push_status = 'failed', hevy_push_error = %s
                where proposal_id = %s::uuid
            """, (str(e), proposal_id))
            return {
                "message": "Proposal approved and applied locally, but pushing to Hevy failed.",
                "approval_result": approved, "apply_result": applied,
                "hevy_push_error": str(e), "routine_title": routine_title
            }

    @mcp.tool()
    def bulk_apply_weight_corrections(
        corrections_json: str,
        reviewed_by: str = "frank"
    ) -> dict:
        """Apply a batch of lb-based weight corrections directly to hevy_routine_sets
        and push each affected routine to Hevy once. Bypasses the proposal system
        for mechanical unit-conversion fixes — no per-row tool calls needed.
 
        corrections_json example:
          [
            {"routine": "Arms1", "exercise": "Bench Press (Barbell)", "set_index": 0, "target_lbs": 190},
            {"routine": "Arms1", "exercise": "Bench Press (Barbell)", "set_index": 1, "target_lbs": 175},
            {"routine": "Legs1", "exercise": "Squat (Barbell)", "set_index": 0, "target_lbs": 255}
          ]
        """
        LBS_TO_KG = 0.45359237
 
        try:
            corrections = json.loads(corrections_json or "[]")
        except Exception as e:
            return {"message": f"Invalid corrections_json: {e}"}
 
        if not isinstance(corrections, list) or not corrections:
            return {"message": "corrections_json must be a non-empty JSON array."}
 
        # Validate all entries up front
        required_keys = {"routine", "exercise", "set_index", "target_lbs"}
        for i, c in enumerate(corrections):
            missing = required_keys - set(c.keys())
            if missing:
                return {"message": f"Correction at index {i} missing keys: {missing}"}
 
        conn = get_write_conn()
        updated = []
        failed = []
        affected_routines = set()
 
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                for c in corrections:
                    weight_kg = round(c["target_lbs"] * LBS_TO_KG, 5)
                    cur.execute("""
                        update hevy_routine_sets rs
                        set weight_kg = %s
                        from hevy_routine_exercises re
                        join hevy_routines r on re.routine_id = r.routine_id
                        where rs.routine_id      = re.routine_id
                          and rs.exercise_index  = re.exercise_index
                          and r.title            = %s
                          and re.title           = %s
                          and rs.set_index       = %s
                        returning
                            r.title  as routine_title,
                            re.title as exercise_name,
                            rs.set_index,
                            rs.weight_kg
                    """, (weight_kg, c["routine"], c["exercise"], c["set_index"]))
                    rows = cur.fetchall()
                    if rows:
                        affected_routines.add(c["routine"])
                        updated.append({
                            "routine":    c["routine"],
                            "exercise":   c["exercise"],
                            "set_index":  c["set_index"],
                            "target_lbs": c["target_lbs"],
                            "stored_kg":  weight_kg,
                        })
                    else:
                        failed.append({
                            "routine":   c["routine"],
                            "exercise":  c["exercise"],
                            "set_index": c["set_index"],
                            "reason":    "No matching row found",
                        })
            conn.commit()
        except Exception as e:
            conn.rollback()
            return {"message": f"Batch update failed, rolled back: {e}"}
        finally:
            conn.close()
 
        # Push each affected routine to Hevy once
        push_results = []
        for routine_title in sorted(affected_routines):
            try:
                push_result = push_routine_to_hevy_internal(routine_title)
                push_results.append({
                    "routine_title": routine_title,
                    "status": "success",
                    "result": push_result,
                })
            except Exception as e:
                push_results.append({
                    "routine_title": routine_title,
                    "status": "failed",
                    "error": str(e),
                })
 
        return {
            "message": f"Bulk correction complete: {len(updated)} updated, {len(failed)} not found.",
            "updated": updated,
            "failed": failed,
            "hevy_push_results": push_results,
        }

    @mcp.tool()
    def approve_and_apply_program_change_proposals(
        proposal_ids_json: str,
        reviewed_by: str = "frank"
    ) -> dict:
        """Approve and apply multiple proposals, then push each affected routine to Hevy once.

        proposal_ids_json example:
          ["uuid-1", "uuid-2", "uuid-3"]"""
        if not dataset_exists("hevy_program_change_proposals"):
            return {"message": "hevy_program_change_proposals table does not exist yet."}

        try:
            proposal_ids = json.loads(proposal_ids_json or "[]")
        except Exception as e:
            return {"message": f"Invalid proposal_ids_json: {e}"}

        if not isinstance(proposal_ids, list) or not proposal_ids:
            return {"message": "proposal_ids_json must be a non-empty JSON array of proposal IDs."}

        results = []
        affected_routines = set()

        for pid in proposal_ids:
            approved = update_program_change_status(pid, "approved", reviewed_by=reviewed_by)
            applied  = apply_program_change_proposal(pid)
            updated_rows = applied.get("updated_rows", [])
            if updated_rows:
                affected_routines.add(updated_rows[0]["routine_title"])
            results.append({"proposal_id": pid, "approval_result": approved, "apply_result": applied})

        push_results = []
        for routine_title in sorted(affected_routines):
            try:
                push_result = push_routine_to_hevy_internal(routine_title)
                push_results.append({"routine_title": routine_title, "status": "success", "result": push_result})
                run_write("""
                    update hevy_program_change_proposals
                    set hevy_push_status = 'success', hevy_pushed_at = now(), hevy_push_error = null
                    where proposal_id = any(%s::uuid[]) and routine_title = %s
                """, (proposal_ids, routine_title))
            except Exception as e:
                push_results.append({"routine_title": routine_title, "status": "failed", "error": str(e)})
                run_write("""
                    update hevy_program_change_proposals
                    set hevy_push_status = 'failed', hevy_push_error = %s
                    where proposal_id = any(%s::uuid[]) and routine_title = %s
                """, (str(e), proposal_ids, routine_title))

        return {
            "message": "Batch approve/apply completed.",
            "proposal_results": results,
            "hevy_push_results": push_results
        }
