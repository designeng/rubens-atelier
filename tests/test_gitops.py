import pytest

from app import gitops

from tests.conftest import git

ID = ("Factory", "factory@example.com")


@pytest.fixture
def empty_origin(tmp_path):
    bare = tmp_path / "empty.git"
    git(tmp_path, "init", "--bare", "-b", "main", str(bare))
    return bare


async def test_clone_seeds_an_empty_repository(tmp_path, empty_origin):
    dest = tmp_path / "ws"
    await gitops.clone(str(empty_origin), dest, ref="main", seed_identity=ID)

    assert git(dest, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert git(dest, "log", "--format=%s %an").strip() == "Initial commit Factory"
    assert git(empty_origin, "rev-parse", "main") == git(dest, "rev-parse", "HEAD")


async def test_second_clone_takes_the_existing_seed(tmp_path, empty_origin):
    await gitops.clone(str(empty_origin), tmp_path / "a", ref="main", seed_identity=ID)
    await gitops.clone(str(empty_origin), tmp_path / "b", ref="main", seed_identity=ID)

    assert git(tmp_path / "a", "rev-parse", "HEAD") == git(tmp_path / "b", "rev-parse", "HEAD")


async def test_lost_seed_race_clones_the_winner(tmp_path, empty_origin, monkeypatch):
    winner = tmp_path / "winner"
    await gitops.clone(str(empty_origin), winner, ref="main", seed_identity=ID)
    # The remote looked empty when checked, but another story pushed before us.
    async def looked_empty(url, token=None):
        return True

    monkeypatch.setattr(gitops, "remote_is_empty", looked_empty)
    await gitops._seed(str(empty_origin), tmp_path / "loser", "main", None, ID)

    assert git(tmp_path / "loser", "rev-parse", "HEAD") == git(winner, "rev-parse", "HEAD")


async def test_missing_branch_on_a_non_empty_repo_still_fails(tmp_path, origin):
    with pytest.raises(gitops.GitError):
        await gitops.clone(str(origin), tmp_path / "ws", ref="nope", seed_identity=ID)


async def test_empty_repository_without_seeding_fails(tmp_path, empty_origin):
    with pytest.raises(gitops.GitError):
        await gitops.clone(str(empty_origin), tmp_path / "ws", ref="main")
