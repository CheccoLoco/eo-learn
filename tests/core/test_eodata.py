"""
Copyright (c) 2017- Sinergise and contributors
For the full list of contributors, see the CREDITS file in the root directory of this source tree.

This source code is licensed under the MIT license, see the LICENSE file in the root directory of this source tree.
"""

from __future__ import annotations

import datetime as dt
import warnings
from typing import Any

import numpy as np
import pytest
from geopandas import GeoDataFrame, GeoSeries

from sentinelhub import CRS, BBox

from eolearn.core import EOPatch, FeatureType
from eolearn.core.constants import TIMESTAMP_COLUMN
from eolearn.core.eodata_io import FeatureIO
from eolearn.core.exceptions import EODeprecationWarning, TemporalDimensionWarning
from eolearn.core.types import Feature, FeaturesSpecification
from eolearn.core.utils.testing import assert_feature_data_equal, generate_eopatch

DUMMY_BBOX = BBox((0, 0, 1, 1), CRS(3857))
DUMMY_TIMESTAMPS = ["2017-03-21"]
# ruff: noqa: NPY002, SLF001


@pytest.fixture(name="mini_eopatch")
def mini_eopatch_fixture() -> EOPatch:
    return generate_eopatch(
        {
            FeatureType.DATA: ["A", "B"],
            FeatureType.MASK: ["C", "D"],
            FeatureType.MASK_TIMELESS: ["E"],
            FeatureType.META_INFO: ["beep"],
        }
    )


def test_numpy_feature_types() -> None:
    eop = EOPatch(bbox=DUMMY_BBOX, timestamps=DUMMY_TIMESTAMPS * 2)

    data_examples = [
        np.zeros((2,) * size, dtype=dtype)
        for size in range(6)
        for dtype in [np.float32, np.float64, float, np.uint8, np.int64, bool]
    ]

    for feature_type in filter(lambda fty: fty.is_array(), FeatureType):
        valid_count = 0

        for data in data_examples:
            try:
                eop[feature_type]["TEST"] = data
                valid_count += 1
            except ValueError:  # noqa: PERF203
                pass

        expected_count = 3 if feature_type.is_discrete() else 6
        assert valid_count == expected_count, f"Feature type {feature_type} should take only a specific type of data"


def test_vector_feature_types() -> None:
    eop = EOPatch(bbox=DUMMY_BBOX)

    invalid_vector_input = [{}, [], 0, None]

    for feature_type in filter(lambda fty: fty.is_vector(), FeatureType):
        for entry in invalid_vector_input:
            with pytest.raises(ValueError):
                # Invalid entry for feature_type should raise an error
                eop[feature_type]["TEST"] = entry

    crs_test = CRS.WGS84.pyproj_crs()
    geo_test = GeoSeries([BBox((1, 2, 3, 4), crs=CRS.WGS84).geometry], crs=crs_test)

    eop.vector_timeless["TEST"] = geo_test
    assert isinstance(eop.vector_timeless["TEST"], GeoDataFrame), "GeoSeries should be parsed into GeoDataFrame"
    assert hasattr(eop.vector_timeless["TEST"], "geometry"), "Feature should have geometry attribute"
    assert eop.vector_timeless["TEST"].crs == crs_test, "GeoDataFrame should still contain the crs"

    with pytest.raises(ValueError):
        # Should fail because there is no TIMESTAMP column
        eop.vector["TEST"] = geo_test


@pytest.mark.parametrize("invalid_bbox", [0, list(range(4)), tuple(range(5)), {}, set(), [1, 2, 4, 3, 4326, 3], "BBox"])
def test_bbox_feature_type(invalid_bbox: Any) -> None:
    with pytest.raises((TypeError, ValueError)):
        EOPatch(bbox=invalid_bbox)

    eop = EOPatch(bbox=DUMMY_BBOX)
    with pytest.raises((TypeError, ValueError)):
        eop.bbox = invalid_bbox


@pytest.mark.parametrize(
    "valid_entry", [["2018-01-01", "15.2.1992"], (dt.datetime(2017, 1, 1, 10, 4, 7), dt.date(2017, 1, 11))]
)
def test_timestamp_valid_feature_type(valid_entry: Any) -> None:
    eop = EOPatch(bbox=DUMMY_BBOX, timestamps=valid_entry)
    eop.timestamps = valid_entry


@pytest.mark.parametrize(
    "invalid_timestamps",
    [
        [dt.datetime(2017, 1, 1, 10, 4, 7), None, dt.datetime(2017, 1, 11, 10, 3, 51)],
        "something",
        dt.datetime(2017, 1, 1, 10, 4, 7),
    ],
)
def test_timestamps_invalid_feature_type(invalid_timestamps: Any) -> None:
    with pytest.raises((ValueError, TypeError)):
        EOPatch(bbox=DUMMY_BBOX, timestamps=invalid_timestamps)

    eop = EOPatch(bbox=DUMMY_BBOX)
    with pytest.raises((ValueError, TypeError)):
        eop.timestamps = invalid_timestamps


def test_invalid_characters():
    with pytest.raises(ValueError):
        EOPatch(bbox=DUMMY_BBOX, mask={"mask.npy": np.arange(3 * 3 * 2).reshape(3, 3, 2)})

    eop = EOPatch(bbox=DUMMY_BBOX)
    with pytest.raises(ValueError):
        eop.data_timeless["mask.npy"] = np.arange(3 * 3 * 2).reshape(3, 3, 2)


def test_invalid_ndim():
    with pytest.raises(ValueError):
        EOPatch(bbox=DUMMY_BBOX, mask_timeless={"A": np.ones((2, 4))})

    eop = EOPatch(bbox=DUMMY_BBOX)
    with pytest.raises(ValueError):
        eop.mask_timeless["A"] = np.ones((2, 4))


def test_repr(test_eopatch_path: str) -> None:
    test_eopatch = EOPatch.load(test_eopatch_path)
    repr_str = repr(test_eopatch)
    assert repr_str.startswith("EOPatch(")
    assert repr_str.endswith(")")
    assert len(repr_str) > 100

    assert repr(EOPatch(bbox=DUMMY_BBOX)) == "EOPatch(\n  bbox=BBox(((0.0, 0.0), (1.0, 1.0)), crs=CRS('3857'))\n)"


def test_repr_no_crs(test_eopatch: EOPatch) -> None:
    test_eopatch.vector_timeless["LULC"].crs = None
    repr_str = test_eopatch.__repr__()
    assert isinstance(repr_str, str)
    assert len(repr_str) > 100, "EOPatch __repr__ must return non-empty string even in case of missing crs"


def test_add_feature() -> None:
    bands = np.arange(2 * 3 * 3 * 2).reshape(2, 3, 3, 2)

    eop = EOPatch(bbox=DUMMY_BBOX, timestamps=DUMMY_TIMESTAMPS * 2)
    eop.data["bands"] = bands

    assert np.array_equal(eop.data["bands"], bands), "Data numpy array not stored"


def test_simplified_feature_operations() -> None:
    bands = np.arange(2 * 3 * 3 * 2).reshape(2, 3, 3, 2)
    feature = FeatureType.DATA, "TEST-BANDS"
    eop = EOPatch(bbox=DUMMY_BBOX, timestamps=DUMMY_TIMESTAMPS * 2)

    eop[feature] = bands
    assert np.array_equal(eop[feature], bands), "Data numpy array not stored"


@pytest.mark.parametrize(
    "feature_to_delete",
    [
        (FeatureType.DATA, "A"),
        (FeatureType.MASK, "C"),
        (FeatureType.MASK_TIMELESS, "E"),
        (FeatureType.META_INFO, "beep"),
    ],
)
def test_delete_existing_feature(feature_to_delete: Feature, mini_eopatch: EOPatch) -> None:
    old = mini_eopatch.copy(deep=True)

    del mini_eopatch[feature_to_delete]
    assert feature_to_delete not in mini_eopatch

    for feature in old.get_features():
        if feature != feature_to_delete:
            assert_feature_data_equal(mini_eopatch[feature], old[feature])


@pytest.mark.parametrize("feature_type", [FeatureType.DATA, FeatureType.META_INFO])
def test_delete_existing_feature_type(feature_type: FeatureType, mini_eopatch: EOPatch) -> None:
    old = mini_eopatch.copy(deep=True)

    del mini_eopatch[feature_type]
    assert feature_type not in mini_eopatch

    for ftype, fname in old.get_features():
        if ftype != feature_type:
            assert_feature_data_equal(old[ftype, fname], mini_eopatch[ftype, fname])


def test_delete_fail_on_nonexisting_feature(mini_eopatch: EOPatch) -> None:
    with pytest.raises(KeyError):
        del mini_eopatch[(FeatureType.DATA, "not_here")]


def test_shallow_copy(test_eopatch: EOPatch) -> None:
    eopatch_copy = test_eopatch.copy()
    assert test_eopatch == eopatch_copy
    assert test_eopatch is not eopatch_copy

    eopatch_copy.mask["CLM"] += 1
    assert test_eopatch == eopatch_copy
    assert test_eopatch.mask["CLM"] is eopatch_copy.mask["CLM"]

    eopatch_copy.timestamps.pop()
    assert test_eopatch != eopatch_copy


def test_deep_copy(test_eopatch: EOPatch) -> None:
    eopatch_copy = test_eopatch.copy(deep=True)
    assert test_eopatch == eopatch_copy
    assert test_eopatch is not eopatch_copy

    eopatch_copy.mask["CLM"] += 1
    assert test_eopatch != eopatch_copy


@pytest.mark.parametrize("deep", [True, False])
def test_copy_full_when_no_featuers_specified(test_eopatch: EOPatch, deep: bool) -> None:
    """In case no features are specified timestamps should be automatically copied."""
    micro_patch = test_eopatch.copy(features=[FeatureType.MASK_TIMELESS], copy_timestamps=True)

    assert (
        micro_patch.copy(
            deep=deep,
        ).timestamps
        is not None
    )
    assert micro_patch.copy(deep=deep, features=[FeatureType.MASK_TIMELESS]).timestamps is None


@pytest.mark.parametrize("features", [..., [(FeatureType.MASK, "CLM")]])
def test_copy_lazy_loaded_patch(test_eopatch_path: str, features: FeaturesSpecification) -> None:
    # shallow copy
    original_eopatch = EOPatch.load(test_eopatch_path, lazy_loading=True)
    copied_eopatch = original_eopatch.copy(features=features)

    original_data = original_eopatch.mask._get_unloaded("CLM")
    assert isinstance(original_data, FeatureIO), "Shallow copying loads the data."
    copied_data = copied_eopatch.mask._get_unloaded("CLM")
    assert original_data is copied_data

    original_mask = original_eopatch.mask["CLM"]
    assert copied_eopatch.mask._get_unloaded("CLM").loaded_value is not None
    copied_mask = copied_eopatch.mask["CLM"]
    assert original_mask is copied_mask

    # deep copy
    original_eopatch = EOPatch.load(test_eopatch_path, lazy_loading=True)
    copied_eopatch = original_eopatch.copy(features=features, deep=True)

    original_data = original_eopatch.mask._get_unloaded("CLM")
    assert isinstance(original_data, FeatureIO), "Deep copying loads the data of source."
    copied_data = copied_eopatch.mask._get_unloaded("CLM")
    assert isinstance(copied_data, FeatureIO), "Deep copying loads the data of target."
    assert original_data is not copied_data, "Deep copying only does a shallow copy of FeatureIO objects."

    mask1 = original_eopatch.mask["CLM"]
    assert copied_eopatch.mask._get_unloaded("CLM").loaded_value is None
    mask2 = copied_eopatch.mask["CLM"]
    assert np.array_equal(mask1, mask2), "Data no longer matches after deep copying."
    assert mask1 is not mask2, "Data was not deep copied."


def test_copy_features(test_eopatch: EOPatch) -> None:
    feature = FeatureType.MASK, "CLM"
    eopatch_copy = test_eopatch.copy(features=[feature])
    assert test_eopatch != eopatch_copy
    assert eopatch_copy[feature] is test_eopatch[feature]
    assert len(eopatch_copy.data) == 0


@pytest.mark.parametrize("deep", [True, False])
@pytest.mark.parametrize(
    ("copy_timestamps", "features", "should_copy"),
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
def test_copy_timestamps(test_eopatch: EOPatch, deep, copy_timestamps, features, should_copy):
    with warnings.catch_warnings():
        if not copy_timestamps:
            warnings.simplefilter("ignore", TemporalDimensionWarning)  # produces temporally ill defined patch
        eopatch_copy = test_eopatch.copy(features=features, deep=deep, copy_timestamps=copy_timestamps)
    assert (eopatch_copy.timestamps is not None) == should_copy


@pytest.mark.parametrize(
    ("ftype", "fname"),
    [
        (FeatureType.DATA, "BANDS-S2-L1C"),
        (FeatureType.MASK, "CLM"),
    ],
)
def test_contains(ftype: FeatureType, fname: str, test_eopatch: EOPatch) -> None:
    assert ftype in test_eopatch
    assert (ftype, fname) in test_eopatch

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", (EODeprecationWarning, TemporalDimensionWarning))
        del test_eopatch[ftype, fname]

    assert (ftype, fname) not in test_eopatch


def test_equals() -> None:
    patch_def = dict(bbox=DUMMY_BBOX, timestamps=DUMMY_TIMESTAMPS * 2)
    eop1 = EOPatch(**patch_def, data={"bands": np.arange(2 * 3 * 3 * 2, dtype=np.float32).reshape(2, 3, 3, 2)})
    eop2 = EOPatch(**patch_def, data={"bands": np.arange(2 * 3 * 3 * 2, dtype=np.float32).reshape(2, 3, 3, 2)})
    assert eop1 == eop2
    assert eop1.data == eop2.data

    eop1.data["bands"][1, ...] = np.nan
    assert eop1 != eop2
    assert eop1.data != eop2.data

    eop2.data["bands"][1, ...] = np.nan
    assert eop1 == eop2

    eop1.data["bands"] = np.reshape(eop1.data["bands"], (2, 3, 2, 3))
    assert eop1 != eop2

    eop2.data["bands"] = np.reshape(eop2.data["bands"], (2, 3, 2, 3))
    eop1.data["bands"] = eop1.data["bands"].astype(np.float16)
    assert eop1 != eop2

    del eop1.data["bands"]
    del eop2.data["bands"]
    assert eop1 == eop2

    eop1.data_timeless["dem"] = np.arange(3 * 3 * 2).reshape(3, 3, 2)
    assert eop1 != eop2


@pytest.fixture(name="eopatch_spatial_dim")
def eopatch_spatial_dim_fixture() -> EOPatch:
    patch = EOPatch(bbox=DUMMY_BBOX, timestamps=DUMMY_TIMESTAMPS * 4)
    patch.data["A"] = np.zeros((4, 2, 3, 4))
    patch.mask["B"] = np.ones((4, 3, 2, 1), dtype=np.uint8)
    patch.mask_timeless["C"] = np.zeros((4, 5, 1), dtype=np.uint8)
    return patch


@pytest.mark.parametrize(
    ("feature", "expected_dim"),
    [
        ((FeatureType.DATA, "A"), (2, 3)),
        ((FeatureType.MASK, "B"), (3, 2)),
        ((FeatureType.MASK_TIMELESS, "C"), (4, 5)),
    ],
)
def test_get_spatial_dimension(feature: Feature, expected_dim: tuple[int, int], eopatch_spatial_dim: EOPatch) -> None:
    assert eopatch_spatial_dim.get_spatial_dimension(*feature) == expected_dim


@pytest.mark.parametrize(
    ("patch", "expected_features"),
    [
        (
            generate_eopatch(
                {
                    FeatureType.DATA: ["A", "B"],
                    FeatureType.MASK: ["C", "D"],
                    FeatureType.MASK_TIMELESS: ["E"],
                    FeatureType.META_INFO: ["beep"],
                }
            ),
            [
                (FeatureType.DATA, "A"),
                (FeatureType.DATA, "B"),
                (FeatureType.MASK, "C"),
                (FeatureType.MASK, "D"),
                (FeatureType.MASK_TIMELESS, "E"),
                (FeatureType.META_INFO, "beep"),
            ],
        ),
        (EOPatch(bbox=DUMMY_BBOX), []),
    ],
)
def test_get_features(patch: EOPatch, expected_features: list[Feature]) -> None:
    assert patch.get_features() == expected_features


@pytest.mark.filterwarnings("ignore::eolearn.core.exceptions.EODeprecationWarning")
def test_timestamp_consolidation() -> None:
    # 10 frames
    timestamps = [
        dt.datetime(2017, 1, 1, 10, 4, 7),
        dt.datetime(2017, 1, 4, 10, 14, 5),
        dt.datetime(2017, 1, 11, 10, 3, 51),
        dt.datetime(2017, 1, 14, 10, 13, 46),
        dt.datetime(2017, 1, 24, 10, 14, 7),
        dt.datetime(2017, 2, 10, 10, 1, 32),
        dt.datetime(2017, 2, 20, 10, 6, 35),
        dt.datetime(2017, 3, 2, 10, 0, 20),
        dt.datetime(2017, 3, 12, 10, 7, 6),
        dt.datetime(2017, 3, 15, 10, 12, 14),
    ]

    data = np.random.rand(10, 100, 100, 3)
    mask = np.random.randint(0, 2, (10, 100, 100, 1))
    mask_timeless = np.random.randint(10, 20, (100, 100, 1))
    scalar = np.random.rand(10, 1)

    eop = EOPatch(
        bbox=DUMMY_BBOX,
        timestamps=timestamps,
        data={"DATA": data},
        mask={"MASK": mask},
        scalar={"SCALAR": scalar},
        mask_timeless={"MASK_TIMELESS": mask_timeless},
    )

    good_timestamps = timestamps.copy()
    del good_timestamps[0]
    del good_timestamps[-1]
    good_timestamps.append(dt.datetime(2017, 12, 1))

    removed_frames = eop.consolidate_timestamps(good_timestamps)

    assert good_timestamps[:-1] == eop.timestamps
    assert len(removed_frames) == 2
    assert timestamps[0] in removed_frames
    assert timestamps[-1] in removed_frames
    assert np.array_equal(data[1:-1, ...], eop.data["DATA"])
    assert np.array_equal(mask[1:-1, ...], eop.mask["MASK"])
    assert np.array_equal(scalar[1:-1, ...], eop.scalar["SCALAR"])
    assert np.array_equal(mask_timeless, eop.mask_timeless["MASK_TIMELESS"])


@pytest.mark.parametrize(
    "method_input",
    [
        ["2017-04-08", "2017-09-17"],
        [1, 2],
        lambda dates: (dt.datetime(2017, 4, 1) < x < dt.datetime(2017, 10, 10) for x in dates),
    ],
)
def test_temporal_subset(method_input):
    eop = generate_eopatch(
        {
            FeatureType.DATA: ["data1", "data2"],
            FeatureType.MASK_TIMELESS: ["mask_timeless"],
            FeatureType.SCALAR_TIMELESS: ["scalar_timeless"],
            FeatureType.MASK: ["mask"],
        },
        timestamps=[
            dt.datetime(2017, 1, 5),
            dt.datetime(2017, 4, 8),
            dt.datetime(2017, 9, 17),
            dt.datetime(2018, 1, 5),
            dt.datetime(2018, 12, 1),
        ],
    )
    vector_data = GeoDataFrame(
        {TIMESTAMP_COLUMN: eop.get_timestamps()}, geometry=[eop.bbox.geometry.buffer(i) for i in range(5)], crs=32633
    )
    eop.vector["vector"] = vector_data
    subset_timestamps = eop.timestamps[1:3]

    subset_eop = eop.temporal_subset(method_input)
    assert subset_eop.timestamps == subset_timestamps
    for feature in eop.get_features():
        if feature[0].is_timeless():
            assert_feature_data_equal(eop[feature], subset_eop[feature])
        elif feature[0].is_array():
            assert_feature_data_equal(eop[feature][1:3, ...], subset_eop[feature])

    assert_feature_data_equal(
        subset_eop.vector["vector"],
        vector_data[1:3],
    )


def test_bbox_none_deprecation():
    with pytest.warns(EODeprecationWarning):
        EOPatch()

    eop = EOPatch(bbox=DUMMY_BBOX)
    assert eop.bbox == DUMMY_BBOX

    with pytest.warns(EODeprecationWarning):
        eop.bbox = None

    assert eop.bbox is None


def test_temporal_warnings():
    # assert no warnings for correct patches
    with warnings.catch_warnings():
        warnings.simplefilter("error")

        EOPatch(bbox=DUMMY_BBOX)
        EOPatch(bbox=DUMMY_BBOX, timestamps=2 * DUMMY_TIMESTAMPS)
        EOPatch(bbox=DUMMY_BBOX, timestamps=2 * DUMMY_TIMESTAMPS, data_timeless={"beep": np.ones((3, 4, 5))})
        EOPatch(bbox=DUMMY_BBOX, timestamps=2 * DUMMY_TIMESTAMPS, data={"beep": np.ones((2, 3, 4, 5))})

    # missmatch in init
    with pytest.warns(TemporalDimensionWarning):
        EOPatch(bbox=DUMMY_BBOX, timestamps=3 * DUMMY_TIMESTAMPS, data={"beep": np.ones((2, 3, 4, 5))})

    # no timestamps in init
    with pytest.warns(TemporalDimensionWarning):
        EOPatch(bbox=DUMMY_BBOX, data={"beep": np.ones((2, 3, 4, 5))})

    # setting to eopatch without timestamps
    patch = EOPatch(bbox=DUMMY_BBOX)
    with pytest.warns(TemporalDimensionWarning):
        patch.data = {"beep": np.ones((2, 3, 4, 5))}
    with pytest.warns(TemporalDimensionWarning):
        patch[FeatureType.MASK, "boop"] = np.ones((2, 3, 4, 5), dtype=int)

    # setting features with wrong dim
    patch = EOPatch(bbox=DUMMY_BBOX, timestamps=3 * DUMMY_TIMESTAMPS)
    with pytest.warns(TemporalDimensionWarning):
        patch.data = {"beep": np.ones((2, 3, 4, 5))}
    with pytest.warns(TemporalDimensionWarning):
        patch[FeatureType.MASK, "boop"] = np.ones((2, 3, 4, 5), dtype=int)

    # switching timestamps to wrong dim
    patch = EOPatch(bbox=DUMMY_BBOX, timestamps=2 * DUMMY_TIMESTAMPS, data={"beep": np.ones((2, 3, 4, 5))})
    with pytest.warns(TemporalDimensionWarning):
        patch.timestamps = 3 * DUMMY_TIMESTAMPS

    # switching timestamps to wrong dim but no temporal features is OK
    patch = EOPatch(bbox=DUMMY_BBOX, timestamps=2 * DUMMY_TIMESTAMPS, data_timeless={"beep": np.ones((2, 3, 4))})
    patch.timestamps = DUMMY_TIMESTAMPS
