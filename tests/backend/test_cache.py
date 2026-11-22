"""
Test ``backend/app/cache.py`` at its seams, ``SemanticCache(client)`` and ``open_cache``, and
``scripts/clear_cache.py``'s ``main``, against a fake Upstash client that records commands and
returns the scores a test sets. No network (C006, the no-network form).
"""

import hashlib
import json
import logging
import socket
from fnmatch import fnmatchcase

import httpx
import pytest
from upstash_redis import Redis
from upstash_redis.errors import UpstashError
from upstash_redis.format import cast_response

from backend.app.cache import SemanticCache, open_cache
from scripts import clear_cache

URL = "https://fake-db.upstash.io"
TOKEN = "fake-token"
RESPONSE = {"route": "correct", "answer": "Add CORSMiddleware [1].", "steps": []}
DAY = 24 * 60 * 60


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every outgoing connection raise, so no test can reach an API."""

    def refuse(*args: object, **kwargs: object):
        raise AssertionError("a test tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


class FakeUpstash(Redis):
    """The SDK's own client with ``execute`` answered from memory, so the SDK builds each command
    and formats each reply. ``scores`` stands in for the server's embeddings; ``error`` is raised
    for every command, as the SDK raises a transport error or an error reply."""

    def __init__(self) -> None:
        super().__init__(URL, TOKEN)  # builds an httpx client; nothing connects
        self.commands: list[list] = []
        self.hashes: dict[str, dict[str, str]] = {}
        self.ttls: dict[str, int] = {}
        self.indexes: dict[str, list] = {}
        self.scores: dict[str, float] = {}
        self.slots: list[str] = []
        self.error: Exception | None = None

    def execute(self, command: list):
        self.commands.append(command)
        if self.error is not None:
            raise self.error
        name, *args = command
        if name == "SEARCH.CREATE":
            index = args[0]
            if index in self.indexes and "EXISTSOK" not in args:
                raise UpstashError(f"ERR index {index} already exists")
            self.indexes.setdefault(index, command)
            reply = "OK"
        elif name == "SEARCH.QUERY":
            index, _query, *options = args
            if index not in self.indexes:
                raise UpstashError(f"ERR index {index} not found")
            limit = options[options.index("LIMIT") + 1]
            nearest = sorted(self.scores.items(), key=lambda item: item[1], reverse=True)
            reply = [[key, str(score)] for key, score in nearest[:limit]]
        elif name == "HSET":
            key, *pairs = args
            fields = dict(zip(pairs[::2], pairs[1::2], strict=True))
            if key not in self.slots:
                self.slots.append(key)
            stored = self.hashes.setdefault(key, {})
            reply = len(fields.keys() - stored.keys())
            stored.update(fields)
        elif name == "HGET":
            key, field = args
            reply = self.hashes.get(key, {}).get(field)
        elif name == "EXPIRE":
            key, seconds = args
            reply = int(key in self.hashes)
            if reply:
                self.ttls[key] = seconds
        elif name == "SCAN":
            cursor, *options = args
            pattern = options[options.index("MATCH") + 1]
            start, end = int(cursor), int(cursor) + 2
            page = self.slots[start:end]
            matched = [key for key in page if key in self.hashes and fnmatchcase(key, pattern)]
            reply = [str(end if end < len(self.slots) else 0), matched]
        elif name == "DEL":
            removed = [key for key in args if key in self.hashes]
            for key in removed:
                del self.hashes[key]
                self.ttls.pop(key, None)
            reply = len(removed)
        else:
            raise AssertionError(f"FakeUpstash does not answer {name}")
        return cast_response(command, reply, self)


def key_for(question: str) -> str:
    return "cache:" + hashlib.sha256(question.encode()).hexdigest()


@pytest.fixture
def fake() -> FakeUpstash:
    return FakeUpstash()


@pytest.fixture
def cache(fake: FakeUpstash) -> SemanticCache:
    cache = SemanticCache(fake)
    cache.ensure_index()
    return cache


def test_constructing_sends_nothing(fake: FakeUpstash):
    SemanticCache(fake)

    assert fake.commands == []


def test_ensure_index_creates_a_hash_index_on_one_hybrid_question_field(fake: FakeUpstash):
    SemanticCache(fake).ensure_index()

    assert fake.indexes == {
        "answers": [
            "SEARCH.CREATE",
            "answers",
            "ON",
            "HASH",
            "PREFIX",
            1,
            "cache:",
            "EXISTSOK",
            "SCHEMA",
            "question",
            "HYBRID",
        ]
    }


def test_ensure_index_leaves_an_existing_index_as_it_is(fake: FakeUpstash):
    fake.indexes["answers"] = ["made by an earlier start"]
    cache = SemanticCache(fake)

    cache.ensure_index()
    cache.ensure_index()

    assert fake.indexes == {"answers": ["made by an earlier start"]}


def test_store_writes_the_question_and_response_under_the_question_hash_for_24h(
    fake: FakeUpstash, cache: SemanticCache
):
    question = "How do I add CORS middleware in FastAPI?"

    cache.store(question, RESPONSE)

    key = key_for(question)
    assert fake.hashes == {key: {"question": question, "response": json.dumps(RESPONSE)}}
    assert json.loads(fake.hashes[key]["response"]) == RESPONSE
    assert fake.ttls == {key: DAY}


def test_storing_the_same_question_twice_writes_one_key(fake: FakeUpstash, cache: SemanticCache):
    question = "How do I use Depends?"

    cache.store(question, {"answer": "first"})
    cache.store(question, RESPONSE)

    assert list(fake.hashes) == [key_for(question)]
    assert json.loads(fake.hashes[key_for(question)]["response"]) == RESPONSE
    assert fake.ttls == {key_for(question): DAY}


@pytest.mark.parametrize(
    ("stored", "asked", "score", "hit"),
    [
        (
            "How do I add CORS middleware in FastAPI?",
            "How can I enable CORS in FastAPI?",
            0.953,
            True,
        ),
        (
            "How do I add CORS middleware in FastAPI?",
            "How can I enable CORS in FastAPI?",
            0.94,
            True,
        ),
        ("How do I use Depends with yield?", "How do I use Depends?", 0.891, False),
    ],
    ids=["paraphrase-hits", "exactly-the-threshold-hits", "different-question-misses"],
)
def test_lookup_serves_the_nearest_answer_only_at_or_above_the_threshold(
    fake: FakeUpstash, cache: SemanticCache, stored: str, asked: str, score: float, hit: bool
):
    cache.store(stored, RESPONSE)
    fake.scores = {key_for(stored): score}

    result = cache.lookup(asked)

    assert result == {"response": RESPONSE if hit else None, "score": score}


def test_lookup_sends_one_ann_query_on_the_question_with_a_limit_of_1(
    fake: FakeUpstash, cache: SemanticCache
):
    asked = "How can I enable CORS in FastAPI?"
    fake.commands.clear()

    cache.lookup(asked)

    assert fake.commands == [
        [
            "SEARCH.QUERY",
            "answers",
            json.dumps({"question": {"$ann": asked}}),
            "LIMIT",
            1,
            "NOCONTENT",
        ]
    ]


def test_lookup_returns_only_the_nearest_of_several(fake: FakeUpstash, cache: SemanticCache):
    cache.store("How do I add CORS middleware in FastAPI?", RESPONSE)
    cache.store("How do I use Depends?", {"answer": "other"})
    fake.scores = {
        key_for("How do I use Depends?"): 0.55,
        key_for("How do I add CORS middleware in FastAPI?"): 0.953,
    }

    assert cache.lookup("How can I enable CORS in FastAPI?") == {
        "response": RESPONSE,
        "score": 0.953,
    }


def test_lookup_on_an_empty_index_returns_no_response_and_no_score(cache: SemanticCache):
    assert cache.lookup("How do I use Depends?") == {"response": None, "score": None}


def test_lookup_misses_a_key_that_expired_before_the_index_dropped_it(
    fake: FakeUpstash, cache: SemanticCache
):
    fake.scores = {key_for("How do I add CORS middleware in FastAPI?"): 0.97}

    result = cache.lookup("How can I enable CORS in FastAPI?")

    assert result == {"response": None, "score": 0.97}


@pytest.mark.parametrize(
    ("url", "token"),
    [(None, None), ("", ""), (URL, None), (None, TOKEN)],
    ids=["unset", "empty", "token-unset", "url-unset"],
)
def test_open_cache_is_off_without_both_settings(
    caplog: pytest.LogCaptureFixture, url: str | None, token: str | None
):
    def factory(*args: str):
        raise AssertionError("open_cache built a client with the cache off")

    with caplog.at_level(logging.INFO, logger="backend.app.cache"):
        result = open_cache(url, token, factory)

    assert result is None
    assert [(r.levelno, r.getMessage().startswith("cache off")) for r in caplog.records] == [
        (logging.INFO, True)
    ]


def test_open_cache_is_off_with_one_warning_naming_the_fix_when_the_database_is_unreachable(
    caplog: pytest.LogCaptureFixture, fake: FakeUpstash
):
    fake.error = httpx.ConnectError("[Errno 8] nodename nor servname provided, or not known")

    with caplog.at_level(logging.INFO, logger="backend.app.cache"):
        result = open_cache(URL, TOKEN, lambda url, token: fake)

    assert result is None
    [record] = caplog.records
    assert record.levelno == logging.WARNING
    assert (
        "make a new database or unset UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN"
        in record.getMessage()
    )


def test_open_cache_returns_a_cache_with_its_index_when_the_database_answers(fake: FakeUpstash):
    built = []

    def factory(url: str, token: str) -> FakeUpstash:
        built.append((url, token))
        return fake

    result = open_cache(URL, TOKEN, factory)

    assert isinstance(result, SemanticCache)
    assert result.client is fake
    assert built == [(URL, TOKEN)]
    assert list(fake.indexes) == ["answers"]


def test_open_cache_raises_an_error_reply_that_is_not_a_connection_failure(fake: FakeUpstash):
    fake.error = UpstashError("WRONGPASS invalid or missing auth token")

    with pytest.raises(UpstashError, match="WRONGPASS"):
        open_cache(URL, TOKEN, lambda url, token: fake)


@pytest.mark.parametrize(
    "error",
    [UpstashError("ERR max requests limit exceeded"), httpx.ConnectError("connection refused")],
    ids=["error-reply", "connection"],
)
@pytest.mark.parametrize("call", ["lookup", "store"])
def test_once_the_cache_is_on_an_error_in_a_lookup_or_a_store_raises(
    fake: FakeUpstash, error: Exception, call: str
):
    cache = open_cache(URL, TOKEN, lambda url, token: fake)
    fake.error = error

    with pytest.raises(type(error)):
        if call == "lookup":
            cache.lookup("How do I use Depends?")
        else:
            cache.store("How do I use Depends?", RESPONSE)


@pytest.fixture
def clear_run(monkeypatch: pytest.MonkeyPatch, fake: FakeUpstash) -> list[tuple[str, str]]:
    """``main`` configures the root logger with ``force=True``, which would remove pytest's log
    capture, and quiets ``httpx`` for the rest of the session; the real ``.env`` is not read."""
    monkeypatch.setattr(clear_cache, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(logging, "basicConfig", lambda *args, **kwargs: None)
    monkeypatch.setattr(logging.getLogger("httpx"), "setLevel", lambda level: None)
    monkeypatch.setenv("UPSTASH_REDIS_REST_URL", URL)
    monkeypatch.setenv("UPSTASH_REDIS_REST_TOKEN", TOKEN)
    built: list[tuple[str, str]] = []

    def factory(url: str, token: str) -> FakeUpstash:
        built.append((url, token))
        return fake

    monkeypatch.setattr(clear_cache, "Redis", factory)
    return built


def test_clear_deletes_every_cache_key_and_no_other_and_keeps_the_index(
    caplog: pytest.LogCaptureFixture, fake: FakeUpstash, clear_run: list
):
    cache = SemanticCache(fake)
    cache.ensure_index()
    # Written first, so the scan's first page matches nothing and its cursor is not yet 0.
    for other in ("turns:1", "cacheless", "session:cache:1"):
        fake.hset(other, values={"question": "kept"})
    for n in range(5):
        cache.store(f"question {n}", RESPONSE)
    fake.commands.clear()

    with caplog.at_level(logging.INFO, logger=clear_cache.__name__):
        clear_cache.main([])

    assert sorted(fake.hashes) == ["cacheless", "session:cache:1", "turns:1"]
    assert list(fake.indexes) == ["answers"]
    assert not any(command[0].startswith("SEARCH.") for command in fake.commands)
    assert clear_run == [(URL, TOKEN)]
    assert caplog.messages == ["deleted 5 cached answers"]


def test_clear_with_nothing_cached_deletes_nothing(
    caplog: pytest.LogCaptureFixture, fake: FakeUpstash, clear_run: list
):
    fake.hset("turns:1", values={"question": "kept"})

    with caplog.at_level(logging.INFO, logger=clear_cache.__name__):
        clear_cache.main([])

    assert list(fake.hashes) == ["turns:1"]
    assert not any(command[0] == "DEL" for command in fake.commands)
    assert caplog.messages == ["deleted 0 cached answers"]


@pytest.mark.parametrize(
    "unset",
    [("UPSTASH_REDIS_REST_URL",), ("UPSTASH_REDIS_REST_TOKEN",), clear_cache.SETTINGS],
    ids=["url", "token", "both"],
)
def test_clear_names_every_missing_setting_and_exits_1_before_building_a_client(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    clear_run: list,
    unset: tuple[str, ...],
):
    for name in unset:
        monkeypatch.delenv(name)

    with (
        caplog.at_level(logging.ERROR, logger=clear_cache.__name__),
        pytest.raises(SystemExit) as exit_info,
    ):
        clear_cache.main([])

    assert exit_info.value.code == 1
    assert clear_run == []
    assert caplog.messages == [f"Missing settings: {', '.join(unset)}; set them in .env"]
