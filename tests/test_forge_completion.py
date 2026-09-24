import subprocess
from pathlib import Path


def test_yield_and_commentary_are_not_final_replies():
    path = Path(__file__).parents[1] / "agent_eval/openshell/forge_completion.mjs"
    assert path.exists(), "gateway must wait for the resumed parent"
    script = f"""import {{terminalReply}} from {str(path.as_uri())!r};
    import assert from 'node:assert/strict';
    assert.equal(terminalReply([{{role:'toolResult',toolName:'sessions_yield'}}]),null);
    assert.equal(terminalReply([{{role:'assistant',stopReason:'toolUse',content:[{{type:'text',text:'Waiting'}}]}}]),null);
    assert.equal(terminalReply([{{role:'assistant',stopReason:'stop',content:[{{type:'text',text:'Done'}}]}}]),'Done');
    assert.equal(terminalReply([{{role:'assistant',stopReason:'stop',content:[{{type:'text',text:'Working',textSignature:JSON.stringify({{phase:'commentary'}})}}]}}]),null);
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script], text=True, capture_output=True
    )
    assert result.returncode == 0, result.stderr
