from models.photo import Photo
from services.downloader import download_photo


class FakeResponse:
    headers = {"Content-Type": "image/jpeg"}

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        yield b"fake-jpeg-data"


def test_download_photo_writes_to_requested_project_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "services.downloader.requests.get", lambda *args, **kwargs: FakeResponse()
    )
    target = tmp_path / "lady-di" / "02-escena-1-yate"
    photo = Photo(
        id="one",
        title="Diana en el yate",
        thumbnail_url="https://images.test/small.jpg",
        image_url="https://images.test/original.jpg",
        original_page_url="https://example.test/story",
    )

    destination = download_photo(photo, target)

    assert destination.parent == target
    assert destination.suffix == ".jpg"
    assert destination.read_bytes() == b"fake-jpeg-data"
    assert photo.local_path == str(destination)
