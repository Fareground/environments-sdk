"""Assets: the catalog is read safely, obeys visibility, is recorded by hash, and survives copies, replays and saves."""
import hashlib
import json
import os

import pytest

import fg_env
from fg_env.sdk import host
from fg_env.sdk.assets import blobs
from fg_env.sdk.errors import ContractError

from asset_fixtures import PDF, REPORT, TRIAL, Reader, hashes, patched, png, trial


def _load(tmp_path, contract=None, **kwargs):
    return fg_env.load(trial(tmp_path, contract), **kwargs)


# -- the catalog -------------------------------------------------------------------------------------------------


def test_files_and_folders_become_assets_with_their_hash_type_and_caption(tmp_path):
    store = _load(tmp_path, seed=1).world.assets
    assert sorted(store.assets) == ["agreement", "photos/dock.png", "photos/seam.png", "report"]
    seam = store.get("photos/seam.png")
    assert (seam.kind, seam.media_type, seam.caption, seam.alt) == ("image", "image/png", "Photo seam.png", "A weld photo")
    assert seam.hash == hashlib.sha256(png(color=(10, 200, 30))).hexdigest()[:32]
    assert (store.get("agreement").kind, store.get("report").media_type) == ("pdf", "text/markdown")


def test_asset_reads_an_assets_details_in_expressions(tmp_path):
    contract = patched(outputs={"caption": "$asset('photos/seam.png').caption", "kind": "$asset($entity(p1).file).type",
                                "size": "$asset(agreement).size", "text": "$asset(report).text",
                                "none": "$asset(null)"})
    result = _load(tmp_path, contract, seed=1).run(rounds=1)
    assert result.outputs == {"caption": "Photo seam.png", "kind": "text", "size": len(PDF), "text": REPORT, "none": None}


def test_a_data_column_of_type_asset_names_files_by_path(tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "mug.png").write_bytes(png())
    (tmp_path / "shop" / "items.csv").write_text("sku,photo\nmug,shop/mug.png\n", encoding="utf-8")
    contract = {"name": "Shop", "inputs": {"items": {"type": "table", "source": "shop/items.csv",
                                                     "columns": {"sku": "text", "photo": "asset"}}},
                "types": {"item": {"props": {"photo": {"type": "asset"}}}, "buyer": {"agent": True}},
                "population": [{"type": "item", "from": "$inputs.items", "id": "{$row.sku}", "props": {"photo": "$row.photo"}}],
                "entities": {"b": {"type": "buyer"}}, "actions": {"look_around": {"by": "buyer"}},
                "outputs": {"media": "$asset($entity(mug).photo).media_type"}}
    path = tmp_path / "shop.json"
    path.write_text(json.dumps(contract))
    env = fg_env.load(path, seed=1)
    assert env.entity("mug")["props"]["photo"] == "shop/mug.png"
    assert env.run(rounds=1).outputs == {"media": "image/png"}


@pytest.mark.parametrize("file, message", [
    ("../outside.png", "must be a path inside the contract's folder"),
    ("/etc/passwd", "must be a path inside the contract's folder"),
    (".secret/key.png", "hidden file"),
    ("files/missing.png", "file not found"),
])
def test_paths_outside_the_contract_folder_are_refused_with_the_path_to_fix(tmp_path, file, message):
    contract = patched(assets={"agreement": {"file": file}})
    with pytest.raises(ContractError, match=message) as info:
        _load(tmp_path / "c", contract)
    assert info.value.issues[0].path == "assets.agreement.file"


def test_a_link_that_leads_outside_the_folder_is_refused(tmp_path):
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(PDF)
    folder = tmp_path / "c"
    path = trial(folder)
    os.symlink(outside, folder / "files" / "linked.pdf")
    path.write_text(json.dumps(patched(assets={"agreement": {"file": "files/linked.pdf"}})))
    with pytest.raises(ContractError, match="leads outside the contract's folder"):
        fg_env.load(path)


def test_content_must_match_the_extension_and_sizes_are_capped(tmp_path):
    path = trial(tmp_path, patched(assets={"report": {"file": "files/fake.png"}}))
    (tmp_path / "files" / "fake.png").write_bytes(b"just text")
    with pytest.raises(ContractError, match="is not a image/png file"):
        fg_env.load(path)
    path.write_text(json.dumps(patched(assets={"agreement": {"file": "files/agreement.pdf", "max_bytes": 10}})))
    with pytest.raises(ContractError, match=f"is {len(PDF)} bytes; the limit for pdf files is 10"):
        fg_env.load(path)


def test_a_contract_without_a_folder_says_where_its_files_come_from(tmp_path):
    with pytest.raises(ContractError, match="need a folder to be read from"):
        fg_env.load(TRIAL)
    trial(tmp_path)
    assert fg_env.load(TRIAL, data_dir=tmp_path, seed=1).world.assets.has("agreement")


def test_check_reports_undeclared_asset_ids_and_file_parameter_mistakes():
    contract = patched(entities={**TRIAL["entities"], "p1": {"type": "exhibit", "props": {"file": "reprot"}}},
                       actions={**TRIAL["actions"],
                                "submit": {"by": "attorney", "params": {"doc": {"type": "file", "kinds": ["video"]},
                                                                        "n": {"type": "int", "kinds": ["image"]}}}})
    messages = {(issue.path, issue.message) for issue in fg_env.check(contract, rounds=0)}
    assert ("entities.p1.props.file", "'reprot' is not a declared asset") in messages
    assert ("actions.submit.params.doc.kinds", "unknown asset type 'video'") in messages
    assert ("actions.submit.params.n", "kinds and max_bytes apply to file parameters") in messages


def test_check_warns_when_a_view_attaches_a_private_asset_to_everyone():
    contract = patched(views={"leaky": {"of": "exhibit", "show": "{title}", "attach": "$it.file"}})
    warnings = [issue for issue in fg_env.check(contract, rounds=0) if issue.path == "views.leaky.attach"]
    assert warnings and "private property 'file'" in warnings[0].message


# -- delivery and visibility -------------------------------------------------------------------------------------


def test_each_side_receives_only_its_own_sealed_exhibit_until_trial_reveals_what_was_offered(tmp_path):
    env = _load(tmp_path, seed=4, exposures=True)
    offered = {}

    def attorney(wake):
        files = {item.id for item in wake.attachments}
        if wake.round == 1:
            own = "report" if wake.me["side"] == "plaintiff" else "photos/seam.png"
            assert files == {"agreement", own}
            exhibit = "p1" if own == "report" else "d1"
            result = wake.call("offer", {"exhibit": exhibit})
            assert [item.id for item in result.attachments] == [own] and own.split("/")[-1] in result.text
            offered[wake.entity_id] = own
        wake.end()

    def judge(wake):
        files = {item.id for item in wake.attachments}
        assert files == ({"agreement"} if wake.round == 1 else {"agreement", *offered.values()})
        wake.end()

    result = env.run({"attorney": attorney, "judge": judge}, rounds=2)
    assert result.status == "running", result.error
    round2 = [w for w in result.exposures["wakes"] if w["round"] == 2]
    reveal = {env.world.assets.get(key).hash for key in offered.values()}
    assert all(reveal <= hashes(wake, "update") for wake in round2)


@pytest.mark.parametrize("seed", range(12))
def test_no_seat_ever_receives_a_file_the_rules_hide_from_it(tmp_path, seed):
    env = _load(tmp_path, seed=seed, exposures=True)
    result = env.run(Reader(seed), rounds=3)
    assert result.status == "completed", result.error
    store = env.world.assets
    own = {"pat": store.get("report").hash, "dana": store.get("photos/seam.png").hash}
    agreement = store.get("agreement").hash
    offered = {store.get(env.entity(e)["props"]["file"]).hash for e in ("p1", "d1") if env.entity(e)["props"]["offered"]}
    for wake in result.exposures["wakes"]:
        allowed = {agreement} | ({own.get(wake["entity"])} if wake["round"] == 1 else offered)
        assert hashes(wake) <= allowed, (wake["entity"], wake["round"], wake.get("assets"))
    assert store.get("photos/dock.png").hash not in {h for wake in result.exposures["wakes"] for h in hashes(wake)}


def test_inspect_shows_public_asset_properties_and_never_another_entitys_private_one(tmp_path):
    contract = patched(types={**TRIAL["types"], "poster": {"inspect": True, "props": {"image": {"type": "asset", "default": "photos/dock.png"}}}},
                       entities={**TRIAL["entities"], "board": {"type": "poster"}})
    env = _load(tmp_path, contract, seed=1)
    seen = {}

    def pat(wake):
        seen["board"] = wake.call("inspect", {"id": "board"})
        seen["d1"] = wake.call("inspect", {"id": "d1"})
        wake.end()

    env.run({"pat": pat, "*": "idle"}, rounds=1)
    assert [item.id for item in seen["board"].attachments] == ["photos/dock.png"]
    assert '[image dock.png: "Photo dock.png"]' in seen["board"].text
    assert seen["d1"].attachments == [] and "file" not in seen["d1"].text


def test_attachments_load_their_bytes_and_the_text_carries_a_compact_reference(tmp_path):
    env = _load(tmp_path, seed=1)
    got = {}

    def pat(wake):
        got["files"] = {item.id: item for item in wake.attachments}
        got["brief"], got["update"] = wake.brief, wake.update
        wake.end()

    env.run({"pat": pat, "*": "idle"}, rounds=1)
    files = got["files"]
    assert files["agreement"].read() == PDF and files["agreement"].type == "pdf"
    assert files["report"].text() == REPORT and files["report"].media_type == "text/markdown"
    assert 'Attached: [pdf agreement.pdf: "The signed agreement"]' in got["brief"]
    assert '- Report [text report.md: "Inspection report"]' in got["update"]


def test_a_sealed_choice_delivers_its_files_with_the_outcome_news(tmp_path):
    stages = [{**TRIAL["stages"][0], "turns": "simultaneous"}, TRIAL["stages"][1]]
    env = _load(tmp_path, patched(stages=stages), seed=1)
    later = {}

    def pat(wake):
        if wake.round == 1:
            wake.call("offer", {"exhibit": "p1"})
        else:
            later["ids"] = [item.id for item in wake.attachments]
            later["update"] = wake.update
        wake.end()

    env.run({"pat": pat, "*": "idle"}, rounds=2)
    assert "report" in later["ids"] and '[text report.md: "Inspection report"]' in later["update"]


# -- recordings, copies and saves --------------------------------------------------------------------------------


def test_recordings_and_snapshots_hold_hashes_never_bytes(tmp_path):
    env = _load(tmp_path, seed=2, exposures=True)
    result = env.run(Reader(2), rounds=2)
    dumped = json.dumps(result.to_dict()) + json.dumps(env.snapshot())
    for data in (PDF, png(color=(10, 200, 30))):
        assert data.hex() not in dumped and __import__("base64").b64encode(data).decode() not in dumped
    assert {row["id"] for row in result.assets["assets"]} == {"agreement", "report", "photos/seam.png", "photos/dock.png"}
    assert any(wake.get("assets") for wake in result.exposures["wakes"])


def test_a_restored_run_continues_exactly_and_keeps_its_asset_references(tmp_path):
    path = trial(tmp_path)
    straight = fg_env.load(path, seed=5, exposures=True).run(Reader(5), rounds=3).to_dict()
    env = fg_env.load(path, seed=5, exposures=True)
    env.run(Reader(5), rounds=1)
    restored = fg_env.Env.restore(path, json.loads(json.dumps(env.snapshot())))
    assert restored.world.assets.to_dict() == env.world.assets.to_dict()
    assert restored.run(Reader(5), rounds=2).to_dict() == straight


def test_clones_and_forks_keep_the_catalog(tmp_path):
    env = _load(tmp_path, seed=6)
    env.run(Reader(6), rounds=1)
    clone = env.clone()
    assert clone.run(Reader(6), rounds=1).to_dict() == env.run(Reader(6), rounds=1).to_dict()
    fork = env.fork(patch={"clock": {"rounds": 5}})
    assert fork.world.assets.to_dict() == env.world.assets.to_dict()
    assert fork.run(Reader(6), rounds=1).status == "running"


def test_a_replay_matches_and_reports_a_file_changed_since_the_recording(tmp_path):
    path = trial(tmp_path)
    recorded = fg_env.load(path, seed=3, exposures=True).run(Reader(3), rounds=2)
    assert fg_env.analysis.trace(recorded).replay(path).ok
    (tmp_path / "files" / "photos" / "seam.png").write_bytes(png(color=(1, 2, 3)))
    replay = fg_env.analysis.trace(recorded).replay(path)
    assert not replay.ok and replay.divergence["what"] == "assets" and "photos/seam.png" in replay.message


def test_a_saved_run_carries_its_files_and_replays_where_they_were_never_read(tmp_path, monkeypatch):
    path = trial(tmp_path / "contract")
    result = fg_env.load(path, seed=3, exposures=True).run(Reader(3), rounds=2)
    result.save(tmp_path / "run.json")
    saved = sorted(p.name for p in (tmp_path / "run.assets").iterdir())
    assert saved == sorted(row["hash"] + os.path.splitext(row["path"])[1] for row in result.assets["assets"])
    monkeypatch.setattr(blobs, "_BYTES", {})
    monkeypatch.setattr(blobs, "_PATHS", {})
    monkeypatch.setattr(blobs, "_FOLDERS", [])
    with pytest.raises(blobs.BlobMissing):
        blobs.read(result.assets["assets"][0]["hash"])
    loaded = fg_env.RunResult.load(tmp_path / "run.json")
    assert blobs.read(result.assets["assets"][0]["hash"])
    assert fg_env.analysis.trace(loaded).replay(path).ok


def test_a_changed_file_is_never_passed_off_as_the_recorded_one(tmp_path):
    path = trial(tmp_path)
    (tmp_path / "files" / "report.md").write_text(f"Only here: {tmp_path}", encoding="utf-8")  # bytes no other test holds
    env = fg_env.load(path, seed=1)
    asset = env.world.assets.get("report")
    (tmp_path / "files" / "report.md").write_text("tampered", encoding="utf-8")
    with pytest.raises(blobs.BlobMissing, match="changed after it was read"):
        env.world.assets.data(asset)


def test_a_direct_copy_of_a_stepped_game_keeps_its_own_asset_index(tmp_path, monkeypatch):
    from fg_env.sdk.game import apply_step, game
    from fg_env.sdk.game import state as game_state
    from fg_env.sdk.stepping import Stepper

    (tmp_path / "photo.png").write_bytes(png())
    contract = {"name": "Photo duel", "clock": {"rounds": 3},
                "game": {"players": "seat", "returns": "1 if $actor.shown != 'photo' else 0"},
                "assets": {"photo": {"file": "photo.png", "caption": "The board"}},
                "types": {"seat": {"agent": True, "props": {"shown": {"type": "asset", "default": "photo"}}}},
                "entities": {"a": {"type": "seat"}, "b": {"type": "seat"}},
                "actions": {"submit_photo": {"by": "seat", "params": {"photo": {"type": "file", "kinds": ["image"]}},
                                             "do": "$actor.shown = $params.photo"},
                            "wait": {"by": "seat"}},
                "views": {"board": {"show": "The board", "attach": "$actor.shown"}}}
    path = tmp_path / "duel.json"
    path.write_text(json.dumps(contract))

    def no_replay(*args, **kwargs):
        raise AssertionError("the copy fell back to replaying the run instead of copying it directly")

    monkeypatch.setattr(game_state, "replayed", no_replay)
    state = game(path, seed=1).new_initial_state()
    assert isinstance(state._run, Stepper)
    child = state.clone()
    submitted = __import__("base64").b64encode(png(color=(1, 99, 7))).decode()
    apply_step(child, {"seat": 0, "tool": "submit_photo", "args": {"photo": {"data": submitted, "name": "mine.png"}}})
    mine, theirs = child._run._run().world.assets, state._run._run().world.assets
    assert mine is not theirs and mine.has("photo") and theirs.has("photo")
    assert [key for key in mine.assets if key.startswith("upload:")] and not any(key.startswith("upload:") for key in theirs.assets)
    child.close()
    state.close()


# -- describing files --------------------------------------------------------------------------------------------


def test_a_describe_host_captions_a_file_once_and_every_copy_reads_the_recorded_answer(tmp_path):
    contract = patched(assets={**TRIAL["assets"], "photos": {**TRIAL["assets"]["photos"], "caption": "", "describe": "vision"}},
                       outputs={"caption": "$asset('photos/seam.png').caption"})
    path = trial(tmp_path, contract)
    vision = host.stubs.StubDescriber(lambda request: {"caption": f"Close-up of {request['asset']['name']}", "text": ""})
    env = host.load(path, hosts={"vision": vision}, seed=1)
    assert len(vision.calls) == 2 and vision.calls[0]["attachments"][0]["data"]
    result = env.run(rounds=1)
    assert result.outputs == {"caption": "Close-up of seam.png"}
    replay = host.load(path, hosts=host.Hosts.replaying(host.tape_of(env)), seed=1)
    assert replay.run(rounds=1).outputs == result.outputs and len(vision.calls) == 2
    restored = fg_env.Env.restore(path, json.loads(json.dumps(env.snapshot())))
    assert restored.run(rounds=1).outputs == {"caption": "Close-up of seam.png"} and len(vision.calls) == 2
    offline = fg_env.load(path, seed=1).run(rounds=1)
    assert offline.outputs == {"caption": "A weld photo"}  # without a host, the declared alt text stands in


def test_guide_documents_assets_in_the_core_guide_and_its_own_part():
    core = fg_env.guide()
    assert "`assets`" in core and "asset" in core and len(core) // 4 < 6_000
    part = fg_env.guide("assets")
    for needle in ("wake.attachments", "`file`", "describe", "run.assets/", "`$asset(ref)`", "media=()"):
        assert needle in part or needle in fg_env.guide("all"), needle
