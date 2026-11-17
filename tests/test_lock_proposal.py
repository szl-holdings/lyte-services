"""Offline contracts for the trusted, proposal-only lock generator."""
from __future__ import annotations
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'tools/propose_ci_locks.py'
spec = importlib.util.spec_from_file_location('propose_ci_locks', SOURCE)
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

class LockProposalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='lyte-lock-contract-')
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name); self.root = self.home/'candidate'; self.root.mkdir()
        (self.root/'requirements/ci').mkdir(parents=True)
        self.write('requirements.txt','fastapi==1.0\n')
        self.write('requirements-test.txt','-r requirements.txt\npytest==9.0\n')
        self.write('requirements/ci/runtime.in','-r ../../requirements.txt\n')
        self.write('requirements/ci/test.in','-r ../../requirements-test.txt\n')
        self.write('requirements/ci/granite-adapter.in','-r ../../requirements-test.txt\npandas==3.1\n')
        for name,_ in g.PLAN:self.write('requirements/ci/'+name+'.txt','# historical header\n')
        self.calls=[]
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden'))
        guard.start();self.addCleanup(guard.stop)

    def write(self,name,data): (self.root/name).write_text(data,encoding='utf-8')
    def run_fake(self,args,**kwargs):
        self.calls.append((args,kwargs))
        dest=Path(kwargs['cwd'])/args[-1]
        dest.write_text('fastapi==1.0 \\\n    --hash=sha256:'+'a'*64+'\n',encoding='utf-8')
        return SimpleNamespace(returncode=0)
    def propose(self,runner=None):
        return g.propose(self.root,self.home/'out','a'*40,'b'*40,runner or self.run_fake)

    def test_fixed_dependency_order(self):
        self.assertEqual([x[0] for x in g.PLAN],['runtime','test','granite-adapter'])
    def test_constraints_follow_previous_closure(self):
        cmds=g.commands();self.assertNotIn('--constraint',cmds[0])
        self.assertIn('runtime.txt',cmds[1]);self.assertIn('test.txt',cmds[2])
    def test_compiler_cannot_read_candidate_config(self):
        self.assertTrue(all('--no-config' in c and '--no-build' in c for c in g.commands()))
    def test_valid_pin_include_graph(self):
        self.assertEqual(g.exact_pins(self.root,'requirements/ci/test.in'),['fastapi==1.0','pytest==9.0'])
    def test_headers_are_never_executable(self):
        for name,_ in g.PLAN:self.write('requirements/ci/'+name+'.txt','# uv pip compile x; touch NEVER\n')
        self.propose()
        self.assertFalse(any('NEVER' in ' '.join(c) for c,_ in self.calls))
        self.assertFalse((self.root/'NEVER').exists())
    def test_shell_execution_disabled(self):
        self.propose();self.assertTrue(all(k['shell'] is False for _,k in self.calls))
    def test_credentials_not_forwarded_to_compiler(self):
        with patch.dict('os.environ',{'GH_TOKEN':'synthetic-secret','HF_TOKEN':'synthetic-secret','UV_INDEX_PASSWORD':'synthetic-secret'}):self.propose()
        self.assertTrue(all(not any('TOKEN' in v or 'PASSWORD' in v for v in k['env']) for _,k in self.calls))
    def test_mutable_revision_rejected_before_compiler(self):
        with self.assertRaises(g.ProposalError):g.propose(self.root,self.home/'out','main','b'*40,self.run_fake)
        self.assertFalse(self.calls)
    def test_direct_url_rejected(self):
        self.write('requirements.txt','pkg @ https://example.invalid/payload.whl\n')
        with self.assertRaises(g.ProposalError):self.propose()
        self.assertFalse(self.calls)
    def test_editable_rejected(self):
        self.write('requirements.txt','-e .\n')
        with self.assertRaises(g.ProposalError):self.propose()
    def test_option_injection_rejected(self):
        self.write('requirements.txt','fastapi==1.0 --index-url https://example.invalid\n')
        with self.assertRaises(g.ProposalError):self.propose()
    def test_include_escape_rejected(self):
        self.write('requirements.txt','-r ../outside.txt\n')
        with self.assertRaises(g.ProposalError):self.propose()
    def test_unknown_include_rejected(self):
        self.write('requirements.txt','-r unreviewed.txt\n')
        with self.assertRaises(g.ProposalError):self.propose()
    def test_cycle_rejected(self):
        self.write('requirements.txt','-r requirements-test.txt\n')
        with self.assertRaises(g.ProposalError):self.propose()
    def test_conflicting_pins_rejected(self):
        self.write('requirements-test.txt','-r requirements.txt\nfastapi==2.0\n')
        with self.assertRaises(g.ProposalError):self.propose()
    def test_missing_input_rejected(self):
        (self.root/'requirements.txt').unlink()
        with self.assertRaises(g.ProposalError):self.propose()
    def test_oversized_input_rejected(self):
        self.write('requirements.txt','#'+('x'*(g.MAX_INPUT_BYTES+1)))
        with self.assertRaises(g.ProposalError):self.propose()
    def test_symlink_signal_rejected(self):
        with patch.object(Path,'is_symlink',return_value=True):
            with self.assertRaises(g.ProposalError):self.propose()
    def test_output_cannot_modify_candidate(self):
        with self.assertRaises(g.ProposalError):g.propose(self.root,self.root/'out','a'*40,'b'*40,self.run_fake)
    def test_no_existing_output_overwrite(self):
        (self.home/'out').mkdir()
        with self.assertRaises(FileExistsError):self.propose()
    def test_compiler_failure_never_creates_approval(self):
        with self.assertRaises(g.ProposalError):self.propose(lambda *a,**k:SimpleNamespace(returncode=2))
        self.assertFalse((self.home/'out/proposal.json').exists())
    def test_timeout_is_not_success(self):
        def fail(*a,**k):raise subprocess.TimeoutExpired(a[0],120)
        with self.assertRaises(subprocess.TimeoutExpired):self.propose(fail)
    def test_candidate_bytes_unchanged(self):
        before={p.relative_to(self.root).as_posix():p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.propose()
        after={p.relative_to(self.root).as_posix():p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before,after)
    def test_proposal_binds_heads_hashes_and_no_authority(self):
        report=self.propose();out=self.home/'out'
        self.assertEqual(report['source_head'],'a'*40);self.assertEqual(report['trusted_policy_head'],'b'*40)
        self.assertEqual(report['patch_sha256'],hashlib.sha256((out/'lockfiles.patch').read_bytes()).hexdigest())
        self.assertFalse(report['approval_granted']);self.assertEqual(report['repository_pushes'],0)
        self.assertEqual(json.loads((out/'proposal.json').read_text())['candidate_writes'],0)
    def test_mutating_input_during_compilation_rejected(self):
        def mutate(*a,**k):
            self.write('requirements.txt','fastapi==2.0\n');return self.run_fake(*a,**k)
        with self.assertRaises(g.ProposalError):self.propose(mutate)
    def test_unexpected_output_options_rejected(self):
        def bad(args,**kwargs):
            (Path(kwargs['cwd'])/args[-1]).write_text('--index-url https://example.invalid\n')
            return SimpleNamespace(returncode=0)
        with self.assertRaises(g.ProposalError):self.propose(bad)

    def test_empty_compiler_output_rejected(self):
        def empty(args,**kwargs):
            (Path(kwargs['cwd'])/args[-1]).write_text('# no observations\n')
            return SimpleNamespace(returncode=0)
        with self.assertRaises(g.ProposalError):self.propose(empty)
    def test_unhashed_compiler_requirement_rejected(self):
        def unhashed(args,**kwargs):
            (Path(kwargs['cwd'])/args[-1]).write_text('fastapi==1.0\n')
            return SimpleNamespace(returncode=0)
        with self.assertRaises(g.ProposalError):self.propose(unhashed)
    def test_hash_without_requirement_rejected(self):
        def orphan(args,**kwargs):
            (Path(kwargs['cwd'])/args[-1]).write_text('--hash=sha256:'+'a'*64+'\n')
            return SimpleNamespace(returncode=0)
        with self.assertRaises(g.ProposalError):self.propose(orphan)
    def test_output_volume_not_captured_in_memory(self):
        self.propose()
        self.assertTrue(all(k['stdout']==subprocess.DEVNULL and k['stderr']==subprocess.DEVNULL for _,k in self.calls))
    def test_workflow_has_no_repository_write_or_auto_commit(self):
        workflow=(SOURCE.parents[1]/'.github/workflows/dependabot-lockfile-reconcile.yml').read_text()
        self.assertIn('contents: read',workflow)
        for forbidden in ('contents: write','persist-credentials: true','git push','git commit','eval "$cmd"','secrets.'):
            self.assertNotIn(forbidden,workflow)
    def test_workflow_binds_both_revisions_and_trusted_helper(self):
        workflow=(SOURCE.parents[1]/'.github/workflows/dependabot-lockfile-reconcile.yml').read_text()
        self.assertIn('ref: ${{ github.event.pull_request.base.sha }}',workflow)
        self.assertIn('ref: ${{ github.event.pull_request.head.sha }}',workflow)
        self.assertIn('$GITHUB_WORKSPACE/trusted/tools/propose_ci_locks.py',workflow)
        self.assertIn('github.event.pull_request.head.repo.full_name == github.repository',workflow)

if __name__=='__main__':unittest.main(verbosity=2)
