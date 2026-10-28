import io

from PIL import Image

from shootagent.store import ShootStore


def test_get_image_applies_exif_orientation(tmp_path):
    # Phone JPEGs store pixels sideways plus an orientation tag; masks are drawn on the upright photo,
    # so the store must hand back the upright pixels too.
    im = Image.new("RGB", (40, 20), (255, 0, 0))
    exif = im.getexif()
    exif[0x0112] = 6  # rotate 90 degrees clockwise to display
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif)
    store = ShootStore(tmp_path)
    shoot = store.create("t")
    blob = store.put_bytes(shoot.id, buf.getvalue(), "jpg")
    assert store.get_image(shoot.id, blob).shape == (40, 20, 3)
