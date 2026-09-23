"""Files reach models as real multimodal parts, agents submit files through tools, and hosts receive evidence."""
import base64
import copy
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

import fg_env
from fg_env import participants
from fg_env import host
from fg_env.expr import Untrusted

from asset_fixtures import PDF, REPORT, TRIAL, patched, png, trial, wav

B64_PDF = base64.b64encode(PDF).decode()
B64_SEAM = base64.b64encode(png(color=(10, 200, 30))).decode()


class FakeAnthropic:
    """Replays scripted tool calls and records every request."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.messages = self

    def create(self, **request):
        self.requests.append(json.loads(json.dumps(request, default=str)))
        blocks = self.script.pop(0) if self.script else []
        content = [NS(type="tool_use", id=f"t{len(self.requests)}{i}", name=name, input=args)
                   for i, (name, args) in enumerate(blocks)] or [NS(type="text", text='{"scores": {}, "rationale": "ok"}')]
        return NS(content=content, usage=NS(input_tokens=1, output_tokens=1, cache_read_input_tokens=0,
                                            cache_creation_input_tokens=0))


class FakeOpenAI:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.chat = NS(completions=self)

    def create(self, **request):
        self.requests.append(json.loads(json.dumps(request, default=str)))
        calls = self.script.pop(0) if self.script else []
        tool_calls = [NS(id=f"c{i}", function=NS(name=name, arguments=json.dumps(args))) for i, (name, args) in enumerate(calls)]
        message = NS(content="" if tool_calls else '{"scores": {}, "rationale": "ok"}', tool_calls=tool_calls or None)
        return NS(choices=[NS(message=message)], usage=NS(prompt_tokens=1, completion_tokens=1))


#: The trial with room for two actions in a turn, so a tool result reaches the model in a second request.
TWO_ACTIONS = patched(stages=[{**TRIAL["stages"][0], "max_actions": 2}, TRIAL["stages"][1]])


def _dana_env(tmp_path, contract=None):
    return fg_env.load(trial(tmp_path, contract), seed=1)


# -- LLM participants send real content ---------------------------------------------------------------------------


def test_anthropic_participant_sends_document_and_image_blocks_after_the_update(tmp_path):
    client = FakeAnthropic([[("offer", {"exhibit": "d1"})], [("end_turn", {})]])
    env = _dana_env(tmp_path, TWO_ACTIONS)
    env.run({"dana": participants.anthropic(client, "claude-x"), "*": "idle"}, rounds=1)
    content = client.requests[0]["messages"][0]["content"]
    assert content[0]["type"] == "text" and "Seam" in content[0]["text"]
    assert content[1:] == [
        {"type": "text", "text": 'Attached: [pdf agreement.pdf: "The signed agreement"]'},
        {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": B64_PDF},
         "title": "agreement.pdf", "context": "The signed agreement"},
        {"type": "text", "text": 'Attached: [image seam.png: "Photo seam.png"]'},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": B64_SEAM},
         "cache_control": {"type": "ephemeral"}},  # the prompt-cache breakpoint is on the latest block
    ]
    result = client.requests[1]["messages"][-1]["content"][0]
    assert result["type"] == "tool_result" and result["content"][0]["type"] == "text"
    assert result["content"][2] == {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": B64_SEAM}}


def test_a_text_only_model_reads_captions_in_the_text_and_no_file_content(tmp_path):
    client = FakeAnthropic([[("end_turn", {})]])
    env = _dana_env(tmp_path)
    env.run({"dana": participants.anthropic(client, "claude-x", media=()), "*": "idle"}, rounds=1)
    [first] = client.requests[0]["messages"][0]["content"]  # the update's text alone
    assert first["type"] == "text" and '[image seam.png: "Photo seam.png"]' in first["text"]
    assert "Attached: [pdf agreement.pdf" in client.requests[0]["system"][0]["text"] and B64_PDF not in json.dumps(client.requests)


def test_a_text_file_is_sent_as_a_plain_text_document(tmp_path):
    client = FakeAnthropic([[("end_turn", {})]])
    env = _dana_env(tmp_path)
    env.run({"pat": participants.anthropic(client, "claude-x", media=("text",)), "*": "idle"}, rounds=1)
    assert client.requests[0]["messages"][0]["content"][1:] == [
        {"type": "text", "text": 'Attached: [text report.md: "Inspection report"]'},
        {"type": "document", "source": {"type": "text", "media_type": "text/plain", "data": REPORT},
         "title": "report.md", "context": "Inspection report", "cache_control": {"type": "ephemeral"}}]


def test_openai_participant_sends_data_urls_file_and_audio_parts_and_tool_files_after_the_tool_messages(tmp_path):
    (tmp_path / "files").mkdir(parents=True)
    (tmp_path / "files" / "call.wav").write_bytes(wav())
    contract = patched(assets={**TRIAL["assets"], "call": {"file": "files/call.wav", "caption": "Voicemail"}},
                       brief={"rules": "Listen.", "attach": "[agreement, call]"}, stages=TWO_ACTIONS["stages"])
    client = FakeOpenAI([[("offer", {"exhibit": "d1"})], []])
    env = _dana_env(tmp_path, contract)
    env.run({"dana": participants.openai(client, "gpt-x"), "*": "idle"}, rounds=1)
    user = client.requests[0]["messages"][1]["content"]
    assert user[1:5] == [
        {"type": "text", "text": 'Attached: [pdf agreement.pdf: "The signed agreement"]'},
        {"type": "file", "file": {"filename": "agreement.pdf", "file_data": f"data:application/pdf;base64,{B64_PDF}"}},
        {"type": "text", "text": 'Attached: [audio call.wav: "Voicemail"]'},
        {"type": "input_audio", "input_audio": {"data": base64.b64encode(wav()).decode(), "format": "wav"}}]
    assert user[6] == {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{B64_SEAM}"}}
    after = client.requests[1]["messages"]
    assert after[-2]["role"] == "tool" and isinstance(after[-2]["content"], str)
    assert after[-1]["role"] == "user" and after[-1]["content"][2]["type"] == "image_url"


def test_media_names_only_types_the_provider_takes():
    with pytest.raises(ValueError, match="media must name attachment types"):
        participants.anthropic(FakeAnthropic([]), "m", media=("audio",))


# -- agents submit files -------------------------------------------------------------------------------------------

SUBMIT = patched(
    types={**TRIAL["types"], "exhibit": {"props": {**TRIAL["types"]["exhibit"]["props"], "file": {"type": "asset"}}}},
    actions={**TRIAL["actions"], "file_photo": {
        "by": "attorney", "params": {"exhibit": {"type": "entity", "of": "exhibit"},
                                     "photo": {"type": "file", "kinds": ["image"], "max_bytes": 5000}},
        "do": ["$params.exhibit.file = $params.photo", {"post": "evidence", "text": "New photo", "file": "$params.photo"}],
        "attach": "$params.photo"}},
    stages=[{"name": "preparation", "actions": ["file_photo", "note"], "max_actions": 3, "max_calls": 12}])


def test_a_tool_call_hands_in_a_file_that_is_stored_by_hash_and_attached_to_an_entity(tmp_path):
    photo = png(color=(5, 6, 7))
    env = fg_env.load(trial(tmp_path, SUBMIT), seed=1, exposures=True)
    got = {}

    def pat(wake):
        got["ok"] = wake.call("file_photo", {"exhibit": "p1", "photo": {"data": base64.b64encode(photo).decode(),
                                                                         "name": "night <shot>.png"}})
        wake.end()

    result = env.run({"pat": pat, "*": "idle"}, rounds=1)
    stored = env.world.assets.get(env.entity("p1")["props"]["file"])
    assert got["ok"].ok and stored.id.startswith("upload:") and stored.owner == "pat" and stored.kind == "image"
    assert isinstance(stored.name, Untrusted) and got["ok"].attachments[0].read() == photo
    assert "«night <shot>.png»" in got["ok"].text
    call = result.exposures["wakes"][0]["calls"][0]
    assert call["args"]["photo"] == {"asset": stored.id} and base64.b64encode(photo).decode() not in json.dumps(result.to_dict())
    assert ["upload", stored.to_dict()] in result.exposures["wakes"][0]["steps"]
    assert fg_env.analysis.trace(result).replay(trial(tmp_path, SUBMIT)).ok


@pytest.mark.parametrize("photo, correction", [
    ({"data": "not base64!"}, "`data` is not valid base64"),
    ({"data": base64.b64encode(PDF).decode()}, "is a pdf file (application/pdf); accepted: image"),
    ({"data": base64.b64encode(png() + bytes(6000)).decode()}, "the limit is 5,000"),
    ({"path": "/etc/passwd"}, "a tool call cannot name a path"),
    ({"asset": "agreement"}, "must be a file"),
    ("photos/seam.png", "must be a file"),
])
def test_a_file_that_cannot_be_accepted_gets_a_correction(tmp_path, photo, correction):
    env = fg_env.load(trial(tmp_path, SUBMIT), seed=1)
    got = {}

    def pat(wake):
        got["result"] = wake.call("file_photo", {"exhibit": "p1", "photo": photo})
        wake.end()

    env.run({"pat": pat, "*": "idle"}, rounds=1)
    assert not got["result"].ok and correction in got["result"].text
    assert env.entity("p1")["props"]["file"] == "report"


def test_a_coded_participant_uploads_bytes_and_a_clone_replays_the_upload(tmp_path):
    env = fg_env.load(trial(tmp_path, SUBMIT), seed=1)
    got = {}

    def pat(wake):
        key = wake.upload(png(color=(9, 9, 9)), "mine.png")
        wake.call("file_photo", {"exhibit": "p1", "photo": {"asset": key}})
        branch = wake.clone()
        got["branch"] = branch.call("file_photo", {"exhibit": "d1", "photo": {"asset": key}})
        branch.close()
        wake.end()

    env.run({"pat": pat, "*": "idle"}, rounds=1)
    assert got["branch"].ok and env.entity("p1")["props"]["file"].startswith("upload:")
    assert env.entity("d1")["props"]["file"] == "photos/seam.png"


def test_file_parameters_have_a_schema_models_can_fill(tmp_path):
    env = fg_env.load(trial(tmp_path, SUBMIT), seed=1)
    schema = env.preview("pat")["tools"][0]["input_schema"]["properties"]["photo"]
    assert schema["type"] == "object" and set(schema["properties"]) == {"data", "text", "name", "asset"}
    assert "A file (image; at most 5,000 bytes)." in schema["description"]


# -- hosts receive files ---------------------------------------------------------------------------------------------

JUDGED = patched(
    actions={**TRIAL["actions"], "argue": {"by": "attorney", "params": {"text": {"type": "text", "max_len": 200}},
                                           "do": [{"host": "bench", "action": "judge", "text": "$params.text",
                                                   "subject": "$actor", "attach": "['photos/seam.png', 'report']"}]}},
    stages=[{"name": "preparation", "actions": ["argue", "note"]}],
    mechanisms={"bench": {"kind": "host", "mode": "judge", "host": "bench", "criteria": {"evidence": {}}}})


def test_a_judge_receives_the_attached_exhibits_and_a_replay_never_asks_again(tmp_path):
    path = trial(tmp_path, JUDGED)
    judge = host.stubs.StubEvaluator()

    def pat(wake):
        wake.call("argue", {"text": "The photo shows no crack."})
        wake.end()

    env = host.load(path, hosts={"bench": judge}, seed=1)
    host.run(env, {"pat": pat, "*": "idle"}, rounds=1)
    files = judge.calls[0]["attachments"]
    assert [(f["id"], f["type"], f["media_type"]) for f in files] == [("photos/seam.png", "image", "image/png"),
                                                                     ("report", "text", "text/markdown")]
    assert files[0]["data"] == B64_SEAM and files[1]["text"] == REPORT and files[0]["caption"] == "Photo seam.png"
    replay = host.load(path, hosts=host.Hosts.replaying(host.tape_of(env)), seed=1)
    host.run(replay, {"pat": pat, "*": "idle"}, rounds=1)
    assert len(judge.calls) == 1 and replay.world.records("bench")[0]["total"] == env.world.records("bench")[0]["total"]


def test_a_game_master_receives_attached_files(tmp_path):
    tavern = json.loads((Path(__file__).parents[1] / "examples" / "contracts" / "host" / "tavern_gm.json").read_text())
    contract = copy.deepcopy(tavern)
    contract["assets"] = {"map": {"file": "map.png", "caption": "A map of the cellar"}}
    contract["actions"] = {"show_map": {"by": "adventurer", "params": {"text": "text"},
                                        "do": {"host": "gm", "action": "resolve", "text": "$params.text", "attach": "map"}}}
    (tmp_path / "map.png").write_bytes(png())
    path = tmp_path / "tavern.json"
    path.write_text(json.dumps(contract))
    gm = host.stubs.StubGameMaster()

    def mira(wake):
        wake.call("show_map", {"text": "I study the map."})
        wake.end()

    env = host.load(path, hosts={"game_master": gm}, seed=1)
    host.run(env, {"mira": mira, "*": "idle"}, rounds=1)
    assert gm.calls[0]["attachments"][0]["name"] == "map.png" and gm.calls[0]["attachments"][0]["caption"] == "A map of the cellar"


def test_reference_host_adapters_send_files_as_multimodal_content():
    request = {"judge": "bench", "text": "Look.", "criteria": [], "attachments": [
        {"id": "photo", "type": "image", "media_type": "image/png", "name": "p.png", "size": 3, "hash": "h", "data": "AAA="},
        {"id": "memo", "type": "text", "media_type": "text/plain", "name": "m.txt", "size": 2, "hash": "g", "text": "hi"}]}
    anthropic = FakeAnthropic([])
    host.adapters.anthropic(anthropic, "claude-x").judge(request)
    content = anthropic.requests[0]["messages"][0]["content"]
    assert "AAA=" not in content[0]["text"] and '"id": "photo"' in content[0]["text"]
    assert content[2] == {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAA="}}
    assert content[4] == {"type": "document", "source": {"type": "text", "media_type": "text/plain", "data": "hi"},
                          "title": "m.txt"}
    openai = FakeOpenAI([])
    host.adapters.openai(openai, "gpt-x").judge(request)
    assert openai.requests[0]["messages"][1]["content"][2] == {"type": "image_url",
                                                               "image_url": {"url": "data:image/png;base64,AAA="}}


def test_reference_host_adapters_describe_a_file():
    class Answering(FakeAnthropic):
        def create(self, **request):
            self.requests.append(request)
            return NS(content=[NS(type="text", text='{"caption": "A red square", "text": ""}')], usage=None)

    client = Answering([])
    answer = host.adapters.anthropic(client, "claude-x").describe(
        {"task": "describe", "asset": {"id": "x"}, "attachments": [
            {"id": "x", "type": "image", "media_type": "image/png", "name": "x.png", "size": 1, "hash": "h", "data": "AAA="}]})
    assert answer == {"caption": "A red square", "text": ""}
    assert "file describer" in client.requests[0]["system"]
