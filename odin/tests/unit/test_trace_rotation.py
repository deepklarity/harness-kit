"""Origin: F45 (traces rotate per attempt mandate). Task #119."""

from pathlib import Path

from odin.orchestrator import Orchestrator


class TestRotateLiveTraceFiles:
    """F45 mandate #4: per-task trace and .out files must rotate per attempt.

    The current production code, ``_reset_live_trace_files``, calls
    ``path.unlink()`` then ``path.touch()`` — which destroys the prior
    attempt's content. The replacement method
    ``_rotate_live_trace_files(trace_file, output_file)`` must rename
    ``task_X.trace.jsonl`` to ``task_X.trace.attempt-N.jsonl`` (and the
    matching ``.out`` to ``.out.attempt-N``), then create a fresh empty
    canonical, so each attempt's data is preserved for post-mortem
    inspection.
    """

    def _get_method(self):
        method = getattr(Orchestrator, "_rotate_live_trace_files", None)
        assert method is not None, (
            "Orchestrator._rotate_live_trace_files is missing — "
            "F45 mandate #4 requires trace rotation per attempt"
        )
        return method

    def test_first_attempt_no_prior_files_creates_fresh_canonical(self, tmp_path):
        """T1: With no prior files, rotation yields empty canonical + no archives.

        Pin against regression: the new rotate method must behave like the old
        reset method when there is no prior attempt to preserve.
        """
        rotate = self._get_method()
        work = Path(tmp_path)
        trace_path = work / "task_X.trace.jsonl"
        output_path = work / "task_X.out"

        rotate(str(trace_path), str(output_path))

        assert trace_path.exists(), "fresh canonical trace must exist"
        assert trace_path.read_text() == "", "fresh canonical trace must be empty"
        assert output_path.exists(), "fresh canonical output must exist"
        assert output_path.read_text() == "", "fresh canonical output must be empty"

        archives = sorted(work.glob("task_X.trace.attempt-*.jsonl"))
        assert archives == [], f"expected no archives on first attempt, found {archives}"
        out_archives = sorted(work.glob("task_X.out.attempt-*"))
        assert out_archives == [], f"expected no .out archives on first attempt, found {out_archives}"

    def test_second_attempt_rotates_prior_canonical_to_attempt_1(self, tmp_path):
        """T2: Prior canonical content must be preserved as attempt-1.{jsonl,out}.

        This is the key regression test. Today's ``unlink() + touch()`` destroys
        the prior content. The rotate method must rename instead.
        """
        rotate = self._get_method()
        work = Path(tmp_path)
        trace_path = work / "task_X.trace.jsonl"
        output_path = work / "task_X.out"

        trace_path.write_text('{"attempt":1,"data":"foo"}')
        output_path.write_text("prior attempt stdout")

        rotate(str(trace_path), str(output_path))

        archive = work / "task_X.trace.attempt-1.jsonl"
        assert archive.exists(), "trace.attempt-1.jsonl must exist after rotation"
        assert archive.read_text() == '{"attempt":1,"data":"foo"}', (
            "prior canonical trace content must be preserved verbatim"
        )

        assert trace_path.exists(), "fresh canonical trace must exist"
        assert trace_path.read_text() == "", "fresh canonical trace must be empty"

        out_archive = work / "task_X.out.attempt-1"
        assert out_archive.exists(), "task_X.out.attempt-1 must exist after rotation"
        assert out_archive.read_text() == "prior attempt stdout", (
            "prior canonical .out content must be preserved verbatim"
        )

        assert output_path.exists(), "fresh canonical output must exist"
        assert output_path.read_text() == "", "fresh canonical output must be empty"

    def test_third_attempt_rotates_to_attempt_2(self, tmp_path):
        """T3: Existing archives are preserved; new archive becomes attempt-2.

        Verifies that archives accumulate rather than overwrite. ``attempt-1``
        must remain untouched and the prior canonical must become ``attempt-2``.
        """
        rotate = self._get_method()
        work = Path(tmp_path)
        trace_path = work / "task_X.trace.jsonl"
        output_path = work / "task_X.out"

        trace_path.write_text('{"attempt":2,"data":"bar"}')
        output_path.write_text("second attempt stdout")
        (work / "task_X.trace.attempt-1.jsonl").write_text('{"attempt":1,"data":"foo"}')
        (work / "task_X.out.attempt-1").write_text("first attempt stdout")

        rotate(str(trace_path), str(output_path))

        assert (work / "task_X.trace.attempt-1.jsonl").read_text() == '{"attempt":1,"data":"foo"}', (
            "attempt-1.jsonl must be untouched"
        )
        assert (work / "task_X.out.attempt-1").read_text() == "first attempt stdout", (
            "out.attempt-1 must be untouched"
        )

        assert (work / "task_X.trace.attempt-2.jsonl").read_text() == '{"attempt":2,"data":"bar"}', (
            "prior canonical must become attempt-2.jsonl"
        )
        assert (work / "task_X.out.attempt-2").read_text() == "second attempt stdout", (
            "prior canonical .out must become out.attempt-2"
        )

        assert trace_path.read_text() == "", "canonical trace must be empty after rotation"
        assert output_path.read_text() == "", "canonical .out must be empty after rotation"

    def test_rotates_output_file_symmetrically(self, tmp_path):
        """T4: The .out file must rotate symmetrically with the trace file.

        This is largely subsumed by T2/T3, but called out separately to make
        the symmetry contract explicit. A bug that rotates only the trace
        would still corrupt the .out file across attempts.
        """
        rotate = self._get_method()
        work = Path(tmp_path)
        trace_path = work / "task_X.trace.jsonl"
        output_path = work / "task_X.out"

        output_path.write_text("hello from attempt 1\nmore output\n")
        trace_path.write_text('{"attempt":1,"event":"start"}')

        rotate(str(trace_path), str(output_path))

        out_archive = work / "task_X.out.attempt-1"
        assert out_archive.exists(), "task_X.out.attempt-1 must exist"
        assert out_archive.read_text() == "hello from attempt 1\nmore output\n", (
            "prior .out content must be preserved"
        )
        assert output_path.exists(), "fresh .out must exist"
        assert output_path.read_text() == "", "fresh .out must be empty"

    def test_index_collision_handled(self, tmp_path):
        """T5: Index collision must not overwrite the existing archive.

        If ``attempt-1.jsonl`` already exists, the prior canonical must become
        ``attempt-2.jsonl`` — the implementation must increment correctly
        rather than blindly taking the first empty slot (which would clobber
        attempt-1 if it existed for some other reason, e.g. an interrupted
        earlier rotation).
        """
        rotate = self._get_method()
        work = Path(tmp_path)
        trace_path = work / "task_X.trace.jsonl"
        output_path = work / "task_X.out"

        prior_canonical_trace = '{"attempt":1,"data":"foo"}'
        prior_canonical_out = "attempt one stdout"
        trace_path.write_text(prior_canonical_trace)
        output_path.write_text(prior_canonical_out)

        existing_archive = work / "task_X.trace.attempt-1.jsonl"
        existing_archive_content = '{"attempt":0,"data":"older"}'
        existing_archive.write_text(existing_archive_content)
        existing_out_archive = work / "task_X.out.attempt-1"
        existing_out_archive_content = "attempt zero stdout"
        existing_out_archive.write_text(existing_out_archive_content)

        rotate(str(trace_path), str(output_path))

        assert existing_archive.read_text() == existing_archive_content, (
            "existing attempt-1.jsonl must NOT be overwritten"
        )
        assert existing_out_archive.read_text() == existing_out_archive_content, (
            "existing out.attempt-1 must NOT be overwritten"
        )

        new_trace_archive = work / "task_X.trace.attempt-2.jsonl"
        assert new_trace_archive.exists(), "prior canonical must become attempt-2.jsonl"
        assert new_trace_archive.read_text() == prior_canonical_trace

        new_out_archive = work / "task_X.out.attempt-2"
        assert new_out_archive.exists(), "prior canonical .out must become out.attempt-2"
        assert new_out_archive.read_text() == prior_canonical_out

        assert trace_path.read_text() == ""
        assert output_path.read_text() == ""
