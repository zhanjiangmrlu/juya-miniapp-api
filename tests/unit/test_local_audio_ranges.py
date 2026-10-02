import pytest
from httpx import ASGITransport, AsyncClient

from juya_miniapp_api.infrastructure.config import Settings
from juya_miniapp_api.main import create_app


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path", ["/local-dev/resources/coffee-audio", "/local-dev/media/audio-evolved.wav"]
)
async def test_local_audio_supports_seek_ranges_and_head(path: str) -> None:
    app = create_app(Settings(environment="local", local_dev_mode=True))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        full = await client.get(path)
        assert full.status_code == 200
        assert full.content[:4] == b"RIFF"
        assert full.headers["accept-ranges"] == "bytes"
        first = await client.get(path, headers={"Range": "bytes=0-99"})
        assert first.status_code == 206
        assert first.content == full.content[:100]
        assert first.headers["content-range"] == f"bytes 0-99/{len(full.content)}"
        middle = await client.get(path, headers={"Range": "bytes=8000-15999"})
        assert middle.status_code == 206
        assert middle.content == full.content[8000:16000]
        suffix = await client.get(path, headers={"Range": "bytes=-100"})
        assert suffix.status_code == 206 and suffix.content == full.content[-100:]
        invalid = await client.get(path, headers={"Range": f"bytes={len(full.content)}-"})
        assert invalid.status_code == 416
        assert invalid.headers["content-range"] == f"bytes */{len(full.content)}"
        head = await client.head(path)
        assert head.status_code == 200 and not head.content
        assert int(head.headers["content-length"]) == len(full.content)
