"""
Copyright (c) 2017- Sinergise and contributors
For the full list of contributors, see the CREDITS file in the root directory of this source tree.

This source code is licensed under the MIT license, see the LICENSE file in the root directory of this source tree.
"""

from __future__ import annotations

import datetime
import os
import sys
import warnings
from typing import Any

import boto3
import fs
import geopandas as gpd
import numpy as np
import pytest
from fs.base import FS
from fs.errors import CreateFailed, ResourceNotFound
from fs.tempfs import TempFS
from fs_s3fs import S3FS
from geopandas import GeoDataFrame
from moto import mock_aws
from numpy.testing import assert_array_equal
from shapely.geometry import Point

from sentinelhub import CRS, BBox

from eolearn.core import EOPatch, FeatureType, LoadTask, OverwritePermission, SaveTask, merge_eopatches
from eolearn.core.constants import TIMESTAMP_COLUMN
from eolearn.core.eodata_io import (
    FeatureIO,
    FeatureIOBBox,
    FeatureIOGeoDf,
    FeatureIOJson,
    FeatureIONumpy,
    FeatureIOTimestamps,
)
from eolearn.core.exceptions import EODeprecationWarning
from eolearn.core.types import FeaturesSpecification
from eolearn.core.utils.parsing import FeatureParser
from eolearn.core.utils.testing import assert_feature_data_equal, generate_eopatch


@mock_aws
def create_mocked_s3fs(bucket_name: str = "mocked-test-bucket") -> S3FS:
    """Creates a new empty mocked s3 bucket. If one such bucket already exists it deletes it first."""
    s3resource = boto3.resource("s3", region_name="eu-central-1")

    bucket = s3resource.Bucket(bucket_name)

    if bucket.creation_date:  # If bucket already exists
        for key in bucket.objects.all():
            key.delete()
        bucket.delete()

    s3resource.create_bucket(Bucket=bucket_name, CreateBucketConfiguration={"LocationConstraint": "eu-central-1"})

    return S3FS(bucket_name=bucket_name)


FS_LOADERS = [TempFS, create_mocked_s3fs]

DUMMY_BBOX = BBox((0, 0, 1, 1), CRS.WGS84)


def _skip_when_appropriate(fs_loader: type[FS] | None, use_zarr: bool) -> None:
    if not use_zarr:
        return
    if "zarr" not in sys.modules:
        pytest.skip(reason="Cannot test zarr without dependencies.")
    if fs_loader is not None and not isinstance(fs_loader, type):  # S3FS because is a lazy fixture and not a class
        # https://github.com/aio-libs/aiobotocore/issues/755
        pytest.skip(reason="Moto3 and aiobotocore (used by s3fs used by zarr) do not interact properly.")


@pytest.fixture(name="_silence_warnings")
def _silence_warnings_fixture():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=EODeprecationWarning)
        yield


@pytest.fixture(name="eopatch")
def eopatch_fixture():
    eopatch = generate_eopatch(
        {
            FeatureType.DATA: ["data"],
            FeatureType.MASK_TIMELESS: ["mask", "mask2"],
            FeatureType.SCALAR: ["my scalar with spaces"],
            FeatureType.SCALAR_TIMELESS: ["my timeless scalar with spaces"],
            FeatureType.META_INFO: ["something", "something-else"],
        }
    )
    eopatch.vector["my-df"] = GeoDataFrame(
        {
            "values": [1, 2],
            TIMESTAMP_COLUMN: [datetime.datetime(2017, 1, 1, 10, 4, 7), datetime.datetime(2017, 1, 4, 10, 14, 5)],
            "geometry": [DUMMY_BBOX.geometry, DUMMY_BBOX.geometry],
        },
        crs=DUMMY_BBOX.crs.pyproj_crs(),
    )
    eopatch.vector_timeless["empty-vector"] = GeoDataFrame(
        {"values": [], "geometry": []}, crs=eopatch.bbox.crs.pyproj_crs()
    )

    return eopatch


@mock_aws
@pytest.mark.parametrize("fs_loader", FS_LOADERS)
@pytest.mark.parametrize("use_zarr", [True, False])
def test_saving_in_empty_folder(eopatch, fs_loader, use_zarr: bool):
    _skip_when_appropriate(fs_loader, use_zarr)
    with fs_loader() as temp_fs:
        if isinstance(temp_fs, TempFS):
            eopatch.save(temp_fs.root_path, use_zarr=use_zarr)
        else:
            eopatch.save("/", filesystem=temp_fs, use_zarr=use_zarr)
        assert temp_fs.exists(f"/mask_timeless/mask.{'zarr' if use_zarr else 'npy.gz'}")

        subfolder = "new-subfolder"
        eopatch.save("new-subfolder", filesystem=temp_fs, use_zarr=use_zarr)
        assert temp_fs.exists(f"/{subfolder}/bbox.geojson")


@mock_aws
@pytest.mark.parametrize("fs_loader", FS_LOADERS)
@pytest.mark.parametrize("use_zarr", [True, False])
@pytest.mark.usefixtures("_silence_warnings")
def test_saving_in_non_empty_folder(eopatch, fs_loader, use_zarr: bool):
    _skip_when_appropriate(fs_loader, use_zarr)
    with fs_loader() as temp_fs:
        empty_file = "foo.txt"

        with temp_fs.open(empty_file, "w"):
            pass

        eopatch.save("/", filesystem=temp_fs, use_zarr=use_zarr)
        assert temp_fs.exists(empty_file)

        eopatch.save(
            "/", overwrite_permission=OverwritePermission.OVERWRITE_PATCH, filesystem=temp_fs, use_zarr=use_zarr
        )
        assert not temp_fs.exists(empty_file)


@mock_aws
@pytest.mark.parametrize("fs_loader", FS_LOADERS)
@pytest.mark.parametrize("use_zarr", [True, False])
@pytest.mark.usefixtures("_silence_warnings")
def test_overwriting_non_empty_folder(eopatch, fs_loader, use_zarr: bool):
    _skip_when_appropriate(fs_loader, use_zarr)
    with fs_loader() as temp_fs:
        eopatch.save("/", filesystem=temp_fs, use_zarr=use_zarr)
        eopatch.save(
            "/", filesystem=temp_fs, overwrite_permission=OverwritePermission.OVERWRITE_FEATURES, use_zarr=use_zarr
        )
        eopatch.save(
            "/", filesystem=temp_fs, overwrite_permission=OverwritePermission.OVERWRITE_PATCH, use_zarr=use_zarr
        )

        add_eopatch = EOPatch(bbox=eopatch.bbox)
        add_eopatch.data_timeless["some data"] = np.empty((3, 3, 2))
        add_eopatch.save("/", filesystem=temp_fs, overwrite_permission=OverwritePermission.ADD_ONLY, use_zarr=use_zarr)
        with pytest.raises(ValueError):
            add_eopatch.save(
                "/", filesystem=temp_fs, overwrite_permission=OverwritePermission.ADD_ONLY, use_zarr=use_zarr
            )

        new_eopatch = EOPatch.load("/", filesystem=temp_fs, lazy_loading=False)
        assert new_eopatch == merge_eopatches(eopatch, add_eopatch)


@mock_aws
@pytest.mark.parametrize("fs_loader", FS_LOADERS)
@pytest.mark.parametrize("use_zarr", [True, False])
@pytest.mark.parametrize(
    ("save_features", "load_features"),
    [
        (..., ...),
        ([(FeatureType.DATA, ...)], [(FeatureType.DATA, ...)]),
        ([(FeatureType.DATA, "data"), FeatureType.MASK_TIMELESS], [(FeatureType.DATA, ...)]),
        ([(FeatureType.META_INFO, ...)], [(FeatureType.META_INFO, "something")]),
        ([(FeatureType.DATA, "data")], ...),
    ],
    ids=str,
)
def test_save_load_partial(
    eopatch: EOPatch,
    fs_loader: type[FS],
    save_features: FeaturesSpecification,
    load_features: FeaturesSpecification,
    use_zarr: bool,
):
    _skip_when_appropriate(fs_loader, use_zarr)
    with fs_loader() as temp_fs:
        eopatch.save("/", features=save_features, filesystem=temp_fs, use_zarr=use_zarr)
        loaded_eopatch = EOPatch.load("/", features=load_features, filesystem=temp_fs)

        # have to check features that have been saved and then loaded (double filtering)
        features_to_load = FeatureParser(load_features).get_features(eopatch)
        for feature in FeatureParser(save_features).get_features(eopatch):
            if feature in features_to_load:
                assert feature in loaded_eopatch
            else:
                assert feature not in loaded_eopatch


@mock_aws
@pytest.mark.parametrize("fs_loader", FS_LOADERS)
@pytest.mark.parametrize("use_zarr", [True, False])
def test_save_add_only_features(eopatch, fs_loader, use_zarr: bool):
    _skip_when_appropriate(fs_loader, use_zarr)
    features = [
        (FeatureType.MASK_TIMELESS, "mask"),
        FeatureType.MASK,
        FeatureType.VECTOR,
        (FeatureType.SCALAR, ...),
        (FeatureType.META_INFO, "something"),
    ]

    with fs_loader() as temp_fs:
        eopatch.save(
            "/",
            filesystem=temp_fs,
            features=features,
            overwrite_permission=OverwritePermission.ADD_ONLY,
            use_zarr=use_zarr,
        )


@mock_aws
@pytest.mark.parametrize("fs_loader", FS_LOADERS)
def test_bbox_always_saved(eopatch, fs_loader):
    with fs_loader() as temp_fs:
        eopatch.save("/", filesystem=temp_fs, features=[FeatureType.DATA])
        assert temp_fs.exists("/bbox.geojson")


@mock_aws
@pytest.mark.parametrize("fs_loader", FS_LOADERS)
@pytest.mark.parametrize(
    ("save_timestamps", "features", "should_save"),
    [
        ("auto", ..., True),
        ("auto", [(FeatureType.MASK_TIMELESS, ...)], False),
        ("auto", [(FeatureType.DATA, ...)], True),
        (False, [(FeatureType.DATA, ...)], False),
        (True, [(FeatureType.DATA, ...)], True),
        (True, [], True),
        (True, {FeatureType.MASK_TIMELESS: ...}, True),
    ],
    ids=str,
)
def test_save_timestamps(eopatch, fs_loader, save_timestamps, features, should_save):
    with fs_loader() as temp_fs:
        eopatch.save("/", filesystem=temp_fs, features=features, save_timestamps=save_timestamps)
        assert temp_fs.exists("/timestamps.json") == should_save


def test_auto_save_load_timestamps(eopatch):
    """Saving and loading with default values should process timestamps."""
    test_patch = EOPatch(bbox=eopatch.bbox, timestamps=eopatch.timestamps)  # no temporal stuff
    with TempFS() as temp_fs:
        test_patch.save("/", filesystem=temp_fs)
        assert temp_fs.exists("/timestamps.json")
        assert EOPatch.load("/", filesystem=temp_fs).timestamps is not None


@mock_aws
@pytest.mark.parametrize("fs_loader", FS_LOADERS)
@pytest.mark.parametrize(
    ("load_timestamps", "features", "should_load"),
    [
        ("auto", ..., True),
        ("auto", [(FeatureType.MASK_TIMELESS, ...)], False),
        ("auto", [(FeatureType.DATA, ...)], True),
        (False, [(FeatureType.DATA, ...)], False),
        (True, [(FeatureType.DATA, ...)], True),
        (True, [], True),
        (True, {FeatureType.MASK_TIMELESS: ...}, True),
    ],
    ids=str,
)
def test_load_timestamps(eopatch, fs_loader, load_timestamps, features, should_load):
    with fs_loader() as temp_fs:
        eopatch.save("/", filesystem=temp_fs)
        loaded_patch = EOPatch.load("/", filesystem=temp_fs, features=features, load_timestamps=load_timestamps)
        assert (loaded_patch.timestamps is not None) == should_load


@pytest.mark.parametrize("features", [..., [FeatureType.DATA_TIMELESS]])
@pytest.mark.filterwarnings("ignore::eolearn.core.exceptions.TemporalDimensionWarning")
def test_load_timestamps_when_nonexistant(eopatch, features):
    with TempFS() as temp_fs:
        eopatch.save("/", filesystem=temp_fs, save_timestamps=False)
        loaded_patch = EOPatch.load("/", filesystem=temp_fs, features=features, load_timestamps="auto")
        assert loaded_patch.timestamps is None
        with pytest.raises(OSError):
            loaded_patch = EOPatch.load("/", filesystem=temp_fs, features=features, load_timestamps=True)


@mock_aws
@pytest.mark.parametrize("fs_loader", FS_LOADERS)
@pytest.mark.parametrize("use_zarr", [True, False])
def test_temporally_empty_patch_io(fs_loader, use_zarr: bool):
    _skip_when_appropriate(fs_loader, use_zarr)
    eopatch = EOPatch(bbox=DUMMY_BBOX, data={"data": np.ones((0, 1, 2, 3))}, timestamps=[])
    with fs_loader() as temp_fs:
        eopatch.save("/", filesystem=temp_fs, use_zarr=use_zarr)
        assert eopatch == EOPatch.load("/", filesystem=temp_fs)


@mock_aws
@pytest.mark.parametrize("fs_loader", FS_LOADERS)
@pytest.mark.parametrize("use_zarr", [True, False])
@pytest.mark.usefixtures("_silence_warnings")
def test_overwrite_failure(fs_loader, use_zarr: bool):
    _skip_when_appropriate(fs_loader, use_zarr)
    eopatch = EOPatch(bbox=DUMMY_BBOX)
    mask = np.arange(3 * 3 * 2).reshape(3, 3, 2)
    eopatch.mask_timeless["mask"] = mask
    eopatch.mask_timeless["Mask"] = mask

    with fs_loader() as temp_fs, pytest.raises(OSError):
        eopatch.save("/", filesystem=temp_fs, use_zarr=use_zarr)

    with fs_loader() as temp_fs:
        eopatch.save(
            "/",
            filesystem=temp_fs,
            features=[(FeatureType.MASK_TIMELESS, "mask")],
            overwrite_permission=OverwritePermission.OVERWRITE_PATCH,
            use_zarr=use_zarr,
        )

        with pytest.raises(OSError):
            eopatch.save(
                "/",
                filesystem=temp_fs,
                features=[(FeatureType.MASK_TIMELESS, "Mask")],
                overwrite_permission=OverwritePermission.ADD_ONLY,
                use_zarr=use_zarr,
            )


@mock_aws
@pytest.mark.parametrize("fs_loader", FS_LOADERS)
@pytest.mark.parametrize("compress_level", [0, 1])
def test_compression_deprecation(eopatch, fs_loader, compress_level: int | None):
    folder = "foo-folder"

    with warnings.catch_warnings():  # make warnings errors
        warnings.simplefilter("error")

        with fs_loader() as temp_fs:
            SaveTask(folder, filesystem=temp_fs)

    with fs_loader() as temp_fs, pytest.warns(EODeprecationWarning):
        SaveTask(folder, filesystem=temp_fs, compress_level=compress_level)

    with fs_loader() as temp_fs, pytest.warns(EODeprecationWarning):
        eopatch.save(folder, filesystem=temp_fs, compress_level=compress_level)


@mock_aws
@pytest.mark.parametrize("fs_loader", FS_LOADERS)
@pytest.mark.parametrize("use_zarr", [True, False])
def test_save_and_load_tasks(eopatch, fs_loader, use_zarr: bool):
    _skip_when_appropriate(fs_loader, use_zarr)
    folder = "foo-folder"
    patch_folder = "patch-folder"
    with fs_loader() as temp_fs:
        temp_fs.makedir(folder)

        save_task = SaveTask(folder, filesystem=temp_fs, use_zarr=use_zarr)
        load_task = LoadTask(folder, filesystem=temp_fs, lazy_loading=False)

        saved_eop = save_task(eopatch, eopatch_folder=patch_folder)
        bbox_path = fs.path.join(folder, patch_folder, "bbox.geojson")
        assert temp_fs.exists(bbox_path)
        assert saved_eop == eopatch

        eop = load_task(eopatch_folder=patch_folder)
        assert eop == eopatch


@mock_aws
@pytest.mark.parametrize("fs_loader", FS_LOADERS)
def test_fail_saving_nonexistent_feature(eopatch, fs_loader):
    features = [(FeatureType.DATA, "nonexistent")]
    with fs_loader() as temp_fs, pytest.raises(ValueError):
        eopatch.save("/", filesystem=temp_fs, features=features)


@mock_aws
@pytest.mark.parametrize("fs_loader", FS_LOADERS)
def test_fail_loading_nonexistent_feature(fs_loader):
    for features in [[(FeatureType.DATA, "nonexistent")], [(FeatureType.META_INFO, "nonexistent")]]:
        with fs_loader() as temp_fs, pytest.raises(OSError):
            EOPatch.load("/", filesystem=temp_fs, features=features)


@mock_aws
@pytest.mark.parametrize("fs_loader", FS_LOADERS)
@pytest.mark.parametrize("use_zarr", [True, False])
def test_nonexistent_location(fs_loader, use_zarr: bool):
    """In the event of a path not existing all save actions should create the path, and loads should fail."""
    _skip_when_appropriate(fs_loader, use_zarr)
    path = "./folder/subfolder/new-eopatch/"
    eopatch = EOPatch(bbox=DUMMY_BBOX)

    # IO on nonexistent path inside a temporary FS
    with fs_loader() as temp_fs:
        with pytest.raises(ResourceNotFound):
            EOPatch.load(path, filesystem=temp_fs)

        eopatch.save(path, filesystem=temp_fs, use_zarr=use_zarr)

    # IO on nonexistent path (no fs specified)
    with TempFS() as temp_fs:
        full_path = os.path.join(temp_fs.root_path, path)
        with pytest.raises(CreateFailed):
            EOPatch.load(full_path)

        load_task = LoadTask(full_path)
        with pytest.raises(CreateFailed):
            load_task.execute()

        eopatch.save(full_path, use_zarr=use_zarr)
        assert os.path.exists(full_path)

    # SaveTask on nonexistent path (no fs specified)
    with TempFS() as temp_fs:
        full_path = os.path.join(temp_fs.root_path, path)
        SaveTask(full_path, use_zarr=use_zarr).execute(eopatch)
        assert os.path.exists(full_path)


@mock_aws
@pytest.mark.parametrize("fs_loader", FS_LOADERS)
def test_cleanup_different_compression(fs_loader, eopatch):
    folder = "foo-folder"
    patch_folder = "patch-folder"
    with fs_loader() as temp_fs:
        temp_fs.makedirs(fs.path.join(folder, patch_folder))
        save_task = SaveTask(folder, filesystem=temp_fs, overwrite_permission="OVERWRITE_FEATURES")

        # need to manually save uncompressed features
        bbox_path = fs.path.join(folder, patch_folder, "bbox.geojson")
        FeatureIOBBox.save(eopatch.bbox, temp_fs, bbox_path, compress_level=0)

        timestamps_path = fs.path.join(folder, patch_folder, "timestamps.json")
        FeatureIOTimestamps.save(eopatch.timestamps, temp_fs, timestamps_path, compress_level=0)

        ftype, fname = (FeatureType.MASK_TIMELESS, "mask")
        temp_fs.makedir(fs.path.join(folder, patch_folder, ftype.value))
        mask_timeless_path = fs.path.join(folder, patch_folder, ftype.value, f"{fname}.npy")
        FeatureIONumpy.save(eopatch[(ftype, fname)], temp_fs, mask_timeless_path, compress_level=0)

        # test EOPatch load of uncompressed features
        EOPatch.load(fs.path.join(folder, patch_folder), filesystem=temp_fs)

        # re-save compressed and check cleanup, bbox and timestamps are not compressed
        save_task(eopatch, eopatch_folder=patch_folder)
        assert temp_fs.exists(bbox_path)
        assert temp_fs.exists(timestamps_path)
        assert temp_fs.exists(mask_timeless_path + ".gz")
        assert not temp_fs.exists(bbox_path + ".gz")
        assert not temp_fs.exists(timestamps_path + ".gz")
        assert not temp_fs.exists(mask_timeless_path)


@mock_aws
@pytest.mark.parametrize("fs_loader", FS_LOADERS)
@pytest.mark.parametrize("use_zarr", [True, False])
@pytest.mark.parametrize("folder_name", ["/", "foo", "foo/bar"])
@pytest.mark.usefixtures("_silence_warnings")
def test_lazy_loading_plus_overwrite_patch(fs_loader, folder_name, eopatch, use_zarr: bool):
    _skip_when_appropriate(fs_loader, use_zarr)
    with fs_loader() as temp_fs:
        eopatch.save(folder_name, filesystem=temp_fs)

        lazy_eopatch = EOPatch.load(folder_name, filesystem=temp_fs, lazy_loading=True)
        lazy_eopatch.data["whatever"] = np.empty((5, 3, 3, 2))
        del lazy_eopatch[FeatureType.MASK_TIMELESS, "mask"]

        lazy_eopatch.save(
            folder_name, filesystem=temp_fs, overwrite_permission=OverwritePermission.OVERWRITE_PATCH, use_zarr=use_zarr
        )
        data_extension = "zarr" if use_zarr else "npy.gz"
        assert temp_fs.exists(fs.path.join(folder_name, "data", f"whatever.{data_extension}"))
        assert not temp_fs.exists(fs.path.join(folder_name, "mask_timeless", f"mask.{data_extension}"))


@pytest.mark.parametrize(
    ("constructor", "data"),
    [
        (FeatureIONumpy, np.zeros(20)),
        (FeatureIONumpy, np.zeros((2, 3, 3, 2), dtype=np.int16)),
        (FeatureIONumpy, np.full((4, 5), fill_value=CRS.POP_WEB)),
        (FeatureIOGeoDf, gpd.GeoDataFrame({"col1": ["name1"], "geometry": [Point(1, 2)]}, crs="EPSG:3857")),
        (FeatureIOGeoDf, gpd.GeoDataFrame({"col1": ["name1"], "geometry": [Point(1, 2)]}, crs="EPSG:32733")),
        (
            FeatureIOGeoDf,
            gpd.GeoDataFrame(
                {
                    "values": [1, 2],
                    TIMESTAMP_COLUMN: [
                        datetime.datetime(2017, 1, 1, 10, 4, 7),
                        datetime.datetime(2017, 1, 4, 10, 14, 5),
                    ],
                    "geometry": [Point(1, 2), Point(2, 1)],
                },
                crs="EPSG:3857",
            ),
        ),
        (FeatureIOJson, {}),
        (FeatureIOJson, {"test": "test1", "test3": {"test": "test1"}}),
        (FeatureIOBBox, BBox((1, 2, 3, 4), CRS.WGS84)),
        (FeatureIOTimestamps, []),
        (FeatureIOTimestamps, [datetime.datetime(2017, 1, 1, 10, 4, 7), datetime.datetime(2017, 1, 4, 10, 14, 5)]),
    ],
)
@pytest.mark.parametrize("compress_level", [0, 1])
def test_feature_io(constructor: type[FeatureIO], data: Any, compress_level: int) -> None:
    """
    Tests verifying that FeatureIO subclasses correctly save, load, and lazy-load data.
    Test cases do not include subfolders, because subfolder management is currently done by the `save_eopatch` function.
    """
    file_extension = constructor.get_file_extension(compress_level=compress_level)
    file_name = "name"
    with TempFS("testing_file_sistem") as temp_fs:
        feat_io = constructor(file_name + file_extension, filesystem=temp_fs)
        constructor.save(data, temp_fs, file_name, compress_level=compress_level)
        loaded_data = feat_io.load()
        assert_feature_data_equal(loaded_data, data)

        temp_fs.remove(file_name + file_extension)
        cache_data = feat_io.load()
        assert_feature_data_equal(loaded_data, cache_data)


def test_zarr_and_numpy_overwrite(eopatch):
    _skip_when_appropriate(None, True)
    save_kwargs = dict(features=[FeatureType.DATA], overwrite_permission=OverwritePermission.OVERWRITE_FEATURES)
    with TempFS() as temp_fs:
        eopatch.save("/", filesystem=temp_fs, use_zarr=True, **save_kwargs)
        assert temp_fs.exists(fs.path.join("data", "data.zarr"))
        eopatch.save("/", filesystem=temp_fs, use_zarr=False, **save_kwargs)
        assert not temp_fs.exists(fs.path.join("data", "data.zarr"))
        assert temp_fs.exists(fs.path.join("data", "data.npy.gz"))
        eopatch.save("/", filesystem=temp_fs, use_zarr=True, **save_kwargs)
        assert temp_fs.exists(fs.path.join("data", "data.zarr"))
        assert not temp_fs.exists(fs.path.join("data", "data.npy.gz"))


@pytest.mark.parametrize("zarr_file_exists", [True, False])
def test_zarr_and_numpy_detect_collisions(eopatch, zarr_file_exists):
    _skip_when_appropriate(None, True)
    save_kwargs = dict(features=[FeatureType.DATA], overwrite_permission=OverwritePermission.ADD_ONLY)
    with TempFS() as temp_fs:
        eopatch.save("/", filesystem=temp_fs, use_zarr=zarr_file_exists, **save_kwargs)
        with pytest.raises(ValueError):
            eopatch.save("/", filesystem=temp_fs, use_zarr=not zarr_file_exists, **save_kwargs)


def test_zarr_and_numpy_combined_loading(eopatch):
    _skip_when_appropriate(None, True)
    with TempFS() as temp_fs:
        eopatch.save("/", filesystem=temp_fs, use_zarr=True)
        eopatch.save(
            "/",
            filesystem=temp_fs,
            features=[(FeatureType.MASK_TIMELESS, "mask2")],
            use_zarr=False,
            overwrite_permission=OverwritePermission.OVERWRITE_FEATURES,
        )
        assert temp_fs.exists("mask_timeless/mask.zarr")
        assert temp_fs.exists("mask_timeless/mask2.npy.gz")
        assert EOPatch.load("/", filesystem=temp_fs) == eopatch


@mock_aws
@pytest.mark.parametrize("fs_loader", FS_LOADERS)
@pytest.mark.parametrize(
    "temporal_selection",
    [None, slice(None, 3), slice(2, 4, 2), [3, 4], lambda ts: [i % 2 == 0 for i, _ in enumerate(ts)]],
)
def test_partial_temporal_loading(fs_loader: type[FS], eopatch: EOPatch, temporal_selection):
    _skip_when_appropriate(fs_loader, True)
    with fs_loader() as temp_fs:
        eopatch.save(path="patch-folder", filesystem=temp_fs, use_zarr=True)

        full_patch = EOPatch.load(path="patch-folder", filesystem=temp_fs)
        partial_patch = EOPatch.load(path="patch-folder", filesystem=temp_fs, temporal_selection=temporal_selection)

        assert full_patch == eopatch

        if temporal_selection is None:
            adjusted_selection = slice(None)
        elif callable(temporal_selection):
            adjusted_selection = temporal_selection(full_patch.get_timestamps())
        else:
            adjusted_selection = temporal_selection

        assert_array_equal(full_patch.data["data"][adjusted_selection, ...], partial_patch.data["data"])
        assert_array_equal(full_patch.mask_timeless["mask"], partial_patch.mask_timeless["mask"])
        assert_array_equal(np.array(full_patch.timestamps)[adjusted_selection], partial_patch.timestamps)


@mock_aws
@pytest.mark.parametrize("fs_loader", FS_LOADERS)
def test_partial_temporal_loading_fails_for_numpy(fs_loader: type[FS], eopatch: EOPatch):
    _skip_when_appropriate(fs_loader, True)
    with fs_loader() as temp_fs:
        eopatch.save(path="patch-folder", filesystem=temp_fs, use_zarr=False)

        with pytest.raises(OSError):
            EOPatch.load(path="patch-folder", filesystem=temp_fs, temporal_selection=[0])


@mock_aws
@pytest.mark.parametrize("fs_loader", FS_LOADERS)
@pytest.mark.parametrize("temporal_selection", [[3, 4, 10]])
def test_partial_temporal_loading_fails_bad_selection(fs_loader: type[FS], eopatch: EOPatch, temporal_selection):
    _skip_when_appropriate(fs_loader, True)
    with fs_loader() as temp_fs:
        eopatch.save(path="patch-folder", filesystem=temp_fs, use_zarr=True)

        with pytest.raises(IndexError):
            EOPatch.load(path="patch-folder", filesystem=temp_fs, temporal_selection=temporal_selection)


@mock_aws
@pytest.mark.parametrize("temporal_selection", [None, slice(None, 3), slice(2, 4, 2), [3, 4]])
def test_partial_temporal_saving_into_existing(eopatch: EOPatch, temporal_selection):
    _skip_when_appropriate(TempFS, True)
    with TempFS() as temp_fs:
        io_kwargs = dict(path="patch-folder", filesystem=temp_fs, overwrite_permission="OVERWRITE_FEATURES")
        eopatch.save(**io_kwargs, use_zarr=True)

        partial_patch = eopatch.copy(deep=True).temporal_subset(np.array(eopatch.timestamps)[temporal_selection or ...])

        partial_patch.data["data"] = np.full_like(partial_patch.data["data"], 2)
        partial_patch.save(**io_kwargs, use_zarr=True, temporal_selection=temporal_selection)

        expected_data = eopatch.data["data"].copy()
        expected_data[temporal_selection] = 2

        loaded_patch = EOPatch.load(path="patch-folder", filesystem=temp_fs)
        assert_array_equal(loaded_patch.data["data"], expected_data)
        assert_array_equal(loaded_patch.timestamps, eopatch.timestamps)


@mock_aws
@pytest.mark.parametrize("dtype", [float, np.float32, np.int8])
def test_partial_temporal_saving_just_timestamps(dtype):
    _skip_when_appropriate(TempFS, True)
    patch_skeleton = generate_eopatch(seed=17)
    partial_patch = EOPatch(
        data={"beep": np.full((2, 117, 97, 3), 2, dtype=dtype)},
        bbox=patch_skeleton.bbox,
        timestamps=[patch_skeleton.timestamps[1], patch_skeleton.timestamps[2]],
    )
    expected_data = np.zeros((5, 117, 97, 3), dtype=dtype)
    expected_data[[1, 2]] = 2

    with TempFS() as temp_fs:
        io_kwargs = dict(path="patch-folder", filesystem=temp_fs, overwrite_permission="OVERWRITE_FEATURES")
        patch_skeleton.save(**io_kwargs, save_timestamps=True)

        partial_patch.save(**io_kwargs, use_zarr=True, temporal_selection="infer")

        loaded_patch = EOPatch.load(path="patch-folder", filesystem=temp_fs)
        assert_array_equal(loaded_patch.data["beep"], expected_data)
        assert_array_equal(loaded_patch.timestamps, patch_skeleton.timestamps)


@mock_aws
def test_partial_temporal_saving_infer(eopatch: EOPatch):
    _skip_when_appropriate(TempFS, True)
    with TempFS() as temp_fs:
        io_kwargs = dict(path="patch-folder", filesystem=temp_fs, overwrite_permission="OVERWRITE_FEATURES")
        eopatch.save(**io_kwargs, use_zarr=True)

        partial_patch = eopatch.copy(deep=True).temporal_subset([1, 3, 4])

        partial_patch.data["data"] = np.full_like(partial_patch.data["data"], 2)
        partial_patch.save(**io_kwargs, use_zarr=True, temporal_selection="infer")

        expected_data = eopatch.data["data"].copy()
        expected_data[[1, 3, 4]] = 2

        loaded_patch = EOPatch.load(path="patch-folder", filesystem=temp_fs)
        assert_array_equal(loaded_patch.data["data"], expected_data)
        assert_array_equal(loaded_patch.timestamps, eopatch.timestamps)


@mock_aws
def test_partial_temporal_saving_fails(eopatch: EOPatch):
    _skip_when_appropriate(TempFS, True)
    with TempFS() as temp_fs:
        io_kwargs = dict(
            path="patch-folder", filesystem=temp_fs, use_zarr=True, overwrite_permission="OVERWRITE_FEATURES"
        )
        with pytest.raises(OSError):
            # patch does not exist yet
            eopatch.save(**io_kwargs, temporal_selection=slice(2, None))

        eopatch.save(**io_kwargs)

        with pytest.raises(ValueError, match="without zarr arrays"):
            # saving without zarr
            eopatch.save(path="patch-folder", filesystem=temp_fs, use_zarr=False, temporal_selection=[1, 2])

        with pytest.raises(ValueError):
            # temporal selection smaller than eopatch to be saved
            eopatch.save(**io_kwargs, temporal_selection=[1, 2])

        with pytest.raises(ValueError):
            # temporal selection outside of saved eopatch
            eopatch.save(**io_kwargs, temporal_selection=slice(12, None))

        with pytest.raises(RuntimeError, match=r"Cannot infer temporal selection.*?has no timestamps"):
            # no timestamps, cannot infer
            EOPatch(bbox=eopatch.bbox).save(**io_kwargs, temporal_selection="infer")

        with pytest.raises(ValueError, match=r"Cannot infer temporal selection.*?subset"):
            # wrong timestamps, cannot infer
            EOPatch(bbox=eopatch.bbox, timestamps=["2012-01-01"]).save(**io_kwargs, temporal_selection="infer")

        EOPatch(bbox=eopatch.bbox).save(path="other-folder", filesystem=temp_fs)
        with pytest.raises(OSError, match=r"Saved EOPatch does not have timestamps"):
            # no timestamps saved
            eopatch.save(path="other-folder", filesystem=temp_fs, use_zarr=True, temporal_selection="infer")


@pytest.mark.parametrize("patch_location", [".", "patch-folder", "some/long/path"])
def test_old_style_meta_info(patch_location):
    with TempFS() as temp_fs:
        EOPatch(bbox=DUMMY_BBOX).save(path=patch_location, filesystem=temp_fs)
        meta_info = {"this": ["list"], "something": "else"}
        file_name, file_extension = f"{patch_location}/meta_info", ".json.gz"
        old_style_io = FeatureIOJson(file_name + file_extension, filesystem=temp_fs)
        old_style_io.save(meta_info, temp_fs, file_name, compress_level=1)

        with pytest.warns(EODeprecationWarning):
            loaded_patch = EOPatch.load(path=patch_location, filesystem=temp_fs)
        assert dict(loaded_patch.meta_info.items()) == meta_info

        loaded_patch.meta_info = {"beep": "boop"}
        loaded_patch.save(path=patch_location, filesystem=temp_fs)
        assert not temp_fs.exists(f"{patch_location}/meta_info.json.gz")
        assert temp_fs.exists(f"{patch_location}/meta_info/beep.json.gz")

        loaded_patch = EOPatch.load(path=patch_location, filesystem=temp_fs)
        assert dict(loaded_patch.meta_info.items()) == {"beep": "boop"}
