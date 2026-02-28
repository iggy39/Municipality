from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from municipality.fetcher import FetchResult
from municipality.migrations import apply_all
from municipality.models import Document, DocumentVersion, PipelineRunStep
from municipality.pipeline import PipelineService

HTML_MAP = {
    "https://site.local/root": """
        <html><head><title>Root</title></head><body>
        <a href='/protocols/2025'>2025</a>
        <a href='/protocols/2026'>2026</a>
        </body></html>
    """,
    "https://site.local/protocols/2025": """
        <html><head><title>2025</title></head><body>
        <a href='/protocols/2025/meeting-a'>Meeting A</a>
        <a href='/protocols/2025/meeting-b'>Meeting B</a>
        </body></html>
    """,
    "https://site.local/protocols/2026": """
        <html><head><title>2026</title></head><body>
        <a href='/protocols/2026/meeting-c'>Meeting C</a>
        </body></html>
    """,
    "https://site.local/protocols/2025/meeting-a": """
        <html><head><title>A</title></head><body>
        <a href='/files/protocol-a.pdf?utm_source=x'>Protocol A</a>
        <a href='/files/attachment-a.pdf'>Attachment A</a>
        <a href='/files/audio-a.mp3'>Audio A</a>
        </body></html>
    """,
    "https://site.local/protocols/2025/meeting-b": """
        <html><head><title>B</title></head><body>
        <a href='/files/protocol-a.pdf'>Duplicate Protocol A</a>
        </body></html>
    """,
    "https://site.local/protocols/2026/meeting-c": """
        <html><head><title>C</title></head><body>
        <a href='/files/protocol-c.pdf?parentMediaID=55'>Protocol C</a>
        </body></html>
    """,
}


class StubFetcher:
    def fetch(self, url: str) -> FetchResult:
        if "protocol-c.pdf" in url:
            return FetchResult(
                ok=False, status_code=None, body=b"", mime=None, reason="TIMEOUT"
            )
        if url.endswith("protocol-a.pdf"):
            return FetchResult(
                ok=True,
                status_code=200,
                body=b"same-binary",
                mime="application/pdf",
                reason=None,
            )
        if url.endswith("attachment-a.pdf"):
            return FetchResult(
                ok=True,
                status_code=200,
                body=b"attachment",
                mime="application/pdf",
                reason=None,
            )
        return FetchResult(
            ok=False, status_code=None, body=b"", mime=None, reason="NOT_FOUND"
        )


def _html_fetcher(url: str) -> str:
    if url not in HTML_MAP:
        raise AssertionError(f"unexpected URL in crawl: {url}")
    return HTML_MAP[url]


def test_end_to_end_rerun_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "m1.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    apply_all(engine, Path("migrations"))

    with Session(engine) as session:
        service = PipelineService(
            session=session,
            html_fetcher=_html_fetcher,
            fetcher=StubFetcher(),
            storage_root=tmp_path / "raw",
        )
        run1 = service.run_crawl("testcity", "https://site.local/root")
        run2 = service.run_crawl("testcity", "https://site.local/root")

        docs = session.execute(select(Document)).scalars().all()
        versions = session.execute(select(DocumentVersion)).scalars().all()
        failed_steps = (
            session.execute(
                select(PipelineRunStep).where(PipelineRunStep.status == "failed")
            )
            .scalars()
            .all()
        )
        skipped_steps = (
            session.execute(
                select(PipelineRunStep).where(
                    PipelineRunStep.detail == "UNSUPPORTED_MIME"
                )
            )
            .scalars()
            .all()
        )

        assert run2 > run1
        assert len(docs) == 4
        assert len(versions) == 2
        assert len(failed_steps) == 2
        assert len(skipped_steps) == 2
