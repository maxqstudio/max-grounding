#!/usr/bin/env python3
"""SW2-23 consumer finalize fail-closed and exact-head regression."""
from __future__ import annotations
import json
import subprocess
import sys
import tempfile
from pathlib import Path
import finalize_consumer as consumer

def git(root: Path, *args: str) -> str:
    p=subprocess.run(["git","-C",str(root),*args],text=True,
        stdout=subprocess.PIPE,stderr=subprocess.STDOUT,check=True)
    return p.stdout.strip()

def write(root: Path, name: str, content: str) -> None:
    p=root/name
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(content,encoding="utf-8",newline="\n")

def commit(root: Path) -> str:
    git(root,"add","-A")
    git(root,"commit","-m","fixture")
    return git(root,"rev-parse","HEAD")

def fixture(root: Path) -> str:
    git(root,"init")
    git(root,"config","user.email","sw2@example.invalid")
    git(root,"config","user.name","SW2 Consumer Contract")
    write(root,".workflow/acceptance.json",json.dumps({
        "schema_version":1,
        "test_commands":["python -m unittest discover -s tests -v"]
    })+"\n")
    write(root,"tests/test_app.py","import unittest\nclass Contract(unittest.TestCase):\n def test_app(self): self.assertTrue(True)\n")
    for _,name,_ in consumer.VALIDATORS:
        write(root,".workflow/tools/"+name,"print('FAKE FIXTURE STUB, NOT REAL VALIDATOR')\n")
    return commit(root)

def expect_error(fn,fragment: str) -> None:
    try:
        fn()
    except ValueError as exc:
        if fragment not in str(exc):
            raise AssertionError((fragment,str(exc))) from exc
    else:
        raise AssertionError("false pass: "+fragment)

def main() -> int:
    with tempfile.TemporaryDirectory(prefix="sw2-consumer-finalize-") as td:
        root=Path(td)
        head=fixture(root)
        planned,meta=consumer.plan(root,head,head)
        assert len(planned)==1+len(consumer.VALIDATORS)
        assert meta["producer_selftests"]=="NOT_APPLICABLE_ON_CONSUMER"
        assert all("scripts/selftest_" not in str(cmd) for _,cmd in planned)
        print("CONSUMER_FINALIZE_PLAN_COMPLETE=PASS")
        expect_error(lambda:consumer.plan(root,"0"*40,head),"CONSUMER_FINALIZE_HEAD_MISMATCH")
        expect_error(lambda:consumer.plan(root,head,"0"*40),"CONSUMER_FINALIZE_ACCEPTED_BASE_NOT_ANCESTOR")
        write(root,"untracked.txt","dirty")
        expect_error(lambda:consumer.plan(root,head,head),"CONSUMER_FINALIZE_WORKTREE_DIRTY")
        (root/"untracked.txt").unlink()
        print("CONSUMER_FINALIZE_EXACT_HEAD_AND_CLEAN=PASS")

        expect_error(lambda:consumer.parse_owner_command("bash -c 'true'"),"TEST_COMMAND_SHELL_FORBIDDEN")
        expect_error(lambda:consumer.parse_owner_command("python -c 'print(1)'"),"TEST_COMMAND_INLINE_CODE_FORBIDDEN")
        expect_error(lambda:consumer.parse_owner_command("python -m unittest && echo pass"),"TEST_COMMAND_SHELL_OPERATOR_FORBIDDEN")
        expect_error(lambda:consumer.parse_owner_command("python -m unittest > proof.txt"),"TEST_COMMAND_SHELL_OPERATOR_FORBIDDEN")
        assert consumer.is_source_test(consumer.parse_owner_command("python -m unittest discover -s tests -v"))
        assert not consumer.is_source_test(consumer.parse_owner_command("python -m compileall -q tests"))
        print("CONSUMER_FINALIZE_COMMAND_INJECTION_REJECTED=PASS")

        original=json.loads((root/".workflow/acceptance.json").read_text())
        for tests,code in [([], "CONSUMER_SOURCE_TEST_COMMANDS_MISSING"),
                           (["python -m compileall -q tests"],"CONSUMER_SOURCE_TEST_AUTHORITY_MISSING")]:
            write(root,".workflow/acceptance.json",json.dumps({**original,"test_commands":tests})+"\n")
            expect_error(lambda:consumer.owner_tests(root),code)
        write(root,".workflow/acceptance.json",json.dumps(original)+"\n")
        print("CONSUMER_FINALIZE_NO_TESTS_FALSE_PASS=REJECTED")

        (root/".workflow/tools/validate_handoff.py").unlink()
        expect_error(lambda:consumer.plan(root,head,head),"CONSUMER_FINALIZE_WORKTREE_DIRTY")
        commit(root)
        newer=git(root,"rev-parse","HEAD")
        expect_error(lambda:consumer.plan(root,newer,head),"MANDATORY_CONSUMER_VALIDATOR_MISSING")
        print("CONSUMER_FINALIZE_MISSING_VALIDATOR_REJECTED=PASS")

    # Synthetic scripts above only test shape/negative paths. The real pinned
    # max-grounding consumer and real validators execute in permanent CI.
    print("CONSUMER_FINALIZE_NEGATIVE_PATHS=PASS")
    print("RESULT=PASS")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
