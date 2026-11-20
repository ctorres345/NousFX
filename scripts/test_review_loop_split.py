#!/usr/bin/env python3
"""NFX-114: the Fable offers and Gemini checks moved from review-loop/SKILL.md to references/, with no rule lost."""
import hashlib
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOOP_DIR = os.path.join(ROOT, "claude", "skills", "review-loop")
MAX_BYTES = 16300  # NFX-192: was 16200 (Cesar chose raising over trimming); NFX-190: was 16100; ANI-194: was 14900 (NFX-124: was 14500); room for the Open the PR step, inline so every project copy has it

# SHA-256 of every non-blank line of the two sections as they stood in SKILL.md before the move (ff401cf).
OLD_FABLE = [
    "3f01d359eb9bd904f47b794bb804c9bef2a45ff222c4f2868b94018c5fa9ced0",
    "053b4eb9e5f5bc25f620538d533b6c046f9c4239f5c9694a200e57b3e13e1a98",
    "1c636dcb1bf28ac26c60ab4df5ef2bb085581aecfc54e96237557710cfbfb075",
    "db867c49385f5a947e4043a98243b3754901907f63728f21d80ee36a49953767",
    "d04852fd805953d350c9971a3ec51dcd408466a287bac26ee8776cb4b3c4252c",
    "e44ccf405bfc8216f855988db9e7b83f7f49ee0ded3b9176f8d9fc8eb29568ce",
    "cc4d36c9164163f9f3422d7597f9d36f076ebd044eb9c030ffe835f25a766ccd",
]
OLD_GEMINI = [
    "8017e6e6acdf0aeae6e4caaca46f897e3630b18d39b3164990fd195c7ca3f555",
    "d7befc3bb7e61d7ce7aca4138a2858e5211e62796983b449c6e6293a72e9f88e",
    "2c58342d7fc14975f0807d0c56bb572f32fc991a5aa19b28ace878fd1ac3a6b8",
    "38fe9553a69ce5e161efbf7ef5403d570ac715ae941bac8642e4b8258e99b46d",
    "23cca02b74f386463efa24796c7a9011b0d2c93f0c38b4d15d5d276205b59b43",
    "98f9815e94db605259edda19ddd4ad612032df35f176707e6184074b04c80c43",
    "490c51ad7e2632ad59d0d9d08cd403e313158a166133c3c07c76a157819dd3ea",
    "e94405a7ab0722ea37f60029b4155b0eeed361eeef33e421bdc7c9b4589ed76c",
    "a7972d5926b879a8de92bd35e62e35ef1c2081e9bec88826d3f1b9a916e16780",
    "fcf7ee55b3d1647ff983cd96ea590e3a7923695da8281dd08a44abc017937bad",
    "28a14e3b61a57ab8afdadcd32215b4df86bf3d6aea1776433626dda3b710ba6d",
    "221ab9aa64b89861bb9d3421aabe202589c755b57b7575181cf3cf553c953d5c",
    "1f888001ec8c984888dbc9a6c1c3b5bf842d668d070d0006c1cae917b3f0bdf2",
    "08c9ada1d824f476f3178fba56c779e13e6bcfe2fb93556ef7183660d3404b29",
    "762aec61f18826a8ca05cfcec4c59f828d7e44a288e281d7207f044a5bcb5656",
    "8a27a2006ac826da45346beb6b183b01be3cec8b78f4c04fd7cefd43d03f9e1c",
]
# NFX-210: the body of "Record the review" as it stood in SKILL.md before the move (0245037). NFX-218 rewrote its
# "the check command itself still runs" sentence (a proof now reuses a matching check stamp), so the hash is of
# that edited line; a later edit to it updates this hash the same way, with its ticket.
OLD_RECORD = [
    "54df02fed1b11bd96f4db2a068f7f970c03476fbb5f05cdcf06a10d5df7035a1",
]


def read(*parts):
    with open(os.path.join(LOOP_DIR, *parts), encoding="utf-8") as f:
        return f.read()


def digests(text):
    return {hashlib.sha256(l.encode()).hexdigest() for l in text.splitlines() if l.strip()}


class ReviewLoopSplit(unittest.TestCase):
    def test_skill_is_under_the_byte_cap(self):
        self.assertLess(os.path.getsize(os.path.join(LOOP_DIR, "SKILL.md")), MAX_BYTES)

    def test_no_moved_line_is_lost(self):
        for name, old in (("fable", OLD_FABLE), ("gemini", OLD_GEMINI), ("record", OLD_RECORD)):
            with self.subTest(ref=name):
                self.assertEqual([h for h in old if h not in digests(read("references", name + ".md"))], [])

    def test_headings_stay_in_skill_as_stubs(self):
        skill = read("SKILL.md")
        self.assertIn("## Fable offers (interactive only)\n", skill)
        self.assertIn("## Gemini checks\n", skill)
        self.assertIn("references/fable.md", skill)
        self.assertIn("references/gemini.md", skill)

    def test_stubs_and_first_lines_name_the_trigger(self):
        skill = read("SKILL.md")
        fable_stub = skill.split("## Fable offers (interactive only)\n")[1].split("\n")[0]
        gemini_stub = skill.split("## Gemini checks\n")[1].split("\n")[0]
        for word in ("Tier 3", "Tier 0/1", "unattended", "`/goal`"):
            self.assertIn(word, fable_stub)
        for word in ("UI changes", "Tier 3", "Argon"):
            self.assertIn(word, gemini_stub)
        fable_first = read("references", "fable.md").split("\n")[0]
        gemini_first = read("references", "gemini.md").split("\n")[0]
        self.assertTrue(fable_first.startswith("Read this when"))
        for word in ("Tier 3", "Interactive only", "/goal"):
            self.assertIn(word, fable_first)
        self.assertTrue(gemini_first.startswith("Read this on"))
        for word in ("`ui` is true", "Tier 3", "final Argon call"):
            self.assertIn(word, gemini_first)

    def test_record_body_left_skill(self):
        # NFX-210: the steps live only in references/record.md; MAX_BYTES stays the only size limit.
        skill = read("SKILL.md")
        self.assertEqual([h for h in OLD_RECORD if h in digests(skill)], [])
        for phrase in ("where `<gate>` is the gate path printed in the gate's block message", "--findings <open high count"):
            self.assertNotIn(phrase, skill)
            self.assertIn(phrase, read("references", "record.md"))

    def test_record_stub_names_the_step_and_its_commands(self):
        skill = read("SKILL.md")
        self.assertIn("## Record the review (last step)\n", skill)
        stub = skill.split("## Record the review (last step)\n")[1].split("\n")[0]
        for word in ("references/record.md", "Last step of every run", "before Open the PR",
                     "(after any Fable final pass) make the final Argon call", "`run-check --stamp`",
                     "`prove.py`", "`--mark`", "`gh pr create`"):
            self.assertIn(word, stub)
        record = read("references", "record.md")
        self.assertTrue(record.startswith("Read this at the last step of every run"))
        for word in ("Record the review", "before Open the PR", "--mark"):
            self.assertIn(word, record.split("\n")[0])

    def test_project_setup_installs_the_references(self):
        with open(os.path.join(ROOT, "claude", "skills", "project-setup", "SKILL.md"), encoding="utf-8") as f:
            self.assertIn("`references/*.md`", f.read())

    def test_the_test_fail_check_is_named_with_its_step_and_its_no_finding(self):
        skill = read("SKILL.md")
        self.assertIn("## Test-fail check\n", skill)
        stub = skill.split("## Test-fail check\n")[1].split("\n")[0]
        for word in ("references/testfail.md", "after Step 0", "Record the review"):
            self.assertIn(word, stub)
        ref = read("references", "testfail.md")
        self.assertTrue(ref.startswith("Read this after Step 0"))
        for word in ("scripts/testfail.py run", "testfail.py attest", "Tests-Fail-Without:", "AC-Proof:"):
            self.assertIn(word, ref)
        no_row = [l for l in ref.splitlines() if l.startswith("| `no` |")]
        self.assertEqual(len(no_row), 1)
        for word in ("`med` finding", "pure-refactor"):
            self.assertIn(word, no_row[0])
        self.assertTrue(os.path.isfile(os.path.join(LOOP_DIR, "scripts", "testfail.py")))

    def test_record_the_review_still_names_the_final_argon_call(self):
        self.assertIn("make the final Argon call", read("SKILL.md"))


if __name__ == "__main__":
    unittest.main()
