"""The contract's `assets` section: files beside the contract that the environment carries."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["AssetSpec"]


class AssetSpec(BaseModel):
    """A file (or a folder of files) beside the contract. Its id is its name here; a folder's files are
    `<name>/<file name>`. Referenced from `asset` properties, record fields and expressions (`$asset(id)`)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    file: str | None = Field(None,
                             description="Path of the file, relative to the contract's folder (or `data_dir=`), inside "
                                         "it.")
    folder: str | None = Field(None,
                               description="Path of a folder inside the contract's folder: every file in it (not "
                                           "subfolders, not hidden files) becomes an asset `<name>/<file name>`.")
    type: str | None = Field(None,
                             description="image | pdf | text | audio | file. Default: from the file extension; with "
                                         "`folder`, only files of this type are taken.")
    caption: str = Field("",
                         description="What the file shows, as agents read it next to the file ({name} is the file "
                                     "name).")
    alt: str = Field("",
                     description="A longer description for readers that cannot see the file (text-only models read "
                                 "it).")
    tags: list[str] = Field(default_factory=list, description="Labels for expressions: `'exhibit' in $asset(id).tags`.")
    max_bytes: int | None = Field(None, ge=1,
                                  description="Largest file accepted (default: by type — image 10 MB, pdf 32 MB, text "
                                              "2 MB, audio 25 MB, file 32 MB).")
    describe: str | None = Field(None,
                                 description="A host (a Describer) that writes a caption and extracted text for the "
                                             "file when the world is built, recorded on the host tape: "
                                             "`$asset(id).caption` and `.text` read it.")
    description: str = ""
