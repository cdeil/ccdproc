# Licensed under a 3-clause BSD style license - see LICENSE.rst
# This module implements the base CCDData class.

import os

import numpy as np
from astropy.io import fits
from astropy.modeling import models
from astropy.units.quantity import Quantity
import astropy.units as u

from numpy.testing import assert_array_equal
from astropy.tests.helper import pytest

from ..ccddata import CCDData, electron, adu
from ..ccdproc import *


# test creating variance
# success expected if u_image * u_gain = u_readnoise
@pytest.mark.parametrize('u_image,u_gain,u_readnoise,expect_succes', [
                         (electron, None, electron, True),
                         (electron, electron, electron, False),
                         (u.adu, electron / u.adu, electron, True),
                         (electron, None, u.dimensionless_unscaled, False),
                         (electron, u.dimensionless_unscaled, electron, True),
                         (u.adu, u.dimensionless_unscaled, electron, False),
                         (u.adu, u.photon / u.adu, electron, False),
                         ])
@pytest.mark.data_size(10)
def test_create_variance(ccd_data, u_image, u_gain, u_readnoise,
                         expect_succes):
    ccd_data.unit = u_image
    if u_gain:
        gain = 2.0 * u_gain
    else:
        gain = None
    readnoise = 5 * u_readnoise
    if expect_succes:
        ccd_var = create_variance(ccd_data, gain=gain, readnoise=readnoise)
        assert ccd_var.uncertainty.array.shape == (10, 10)
        assert ccd_var.uncertainty.array.size == 100
        assert ccd_var.uncertainty.array.dtype == np.dtype(float)
        if gain:
            expected_var = np.sqrt(2 * ccd_data.data + 5 ** 2) / 2
        else:
            expected_var = np.sqrt(ccd_data.data + 5 ** 2)
        np.testing.assert_array_equal(ccd_var.uncertainty.array,
                                      expected_var)
        assert ccd_var.unit == ccd_data.unit
        # uncertainty should *not* have any units -- does it?
        with pytest.raises(AttributeError):
            ccd_var.uncertainty.array.unit
    else:
        with pytest.raises(u.UnitsError):
            ccd_var = create_variance(ccd_data, gain=gain, readnoise=readnoise)


def test_create_variance_keywords_must_have_unit(ccd_data):
    # gain must have units if provided
    with pytest.raises(TypeError):
        create_variance(ccd_data, gain=3)
    # readnoise must have units
    with pytest.raises(TypeError):
        create_variance(ccd_data, readnoise=5)
    # readnoise must be provided
    with pytest.raises(ValueError):
        create_variance(ccd_data)


# tests for overscan
@pytest.mark.parametrize('median,model', [
                         (False, None),
                         (True, None), ])
def test_subtract_overscan_mean(ccd_data, median, model):
    # create the overscan region
    oscan = 300.
    oscan_region = (slice(None), slice(0, 10))  # indices 0 through 9
    fits_section = '[1:10, :]'
    science_region = (slice(None), slice(10, None))
    ccd_data.data[oscan_region] = oscan
    # Add a fake sky background so the "science" part of the image has a
    # different average than the "overscan" part.
    sky = 10.
    original_mean = ccd_data.data[science_region].mean()
    ccd_data.data[science_region] += oscan + sky
    # Test once using the overscan argument to specify the overscan region
    ccd_data_overscan = subtract_overscan(ccd_data,
                                          overscan=ccd_data[:, 0:10],
                                          median=median, model=model)
    # Is the mean of the "science" region the sum of sky and the mean the
    # "science" section had before backgrounds were added?
    np.testing.assert_almost_equal(
        ccd_data_overscan.data[science_region].mean(),
        sky + original_mean)
    # Is the overscan region zero?
    assert (ccd_data_overscan.data[oscan_region] == 0).all()

    # Now do what should be the same subtraction, with the overscan specified
    # with the fits_section
    ccd_data_fits_section = subtract_overscan(ccd_data,
                                              fits_section=fits_section,
                                              median=median, model=model)
    # Is the mean of the "science" region the sum of sky and the mean the
    # "science" section had before backgrounds were added?
    np.testing.assert_almost_equal(
        ccd_data_fits_section.data[science_region].mean(),
        sky + original_mean)
    # Is the overscan region zero?
    assert (ccd_data_fits_section.data[oscan_region] == 0).all()

    # Do both ways of subtracting overscan give exactly the same result?
    np.testing.assert_array_equal(ccd_data_overscan[science_region],
                                  ccd_data_fits_section[science_region])


# A more substantial test of overscan modeling
def test_subtract_overscan_model(ccd_data):
    # create the overscan region
    size = ccd_data.shape[0]
    yscan, xscan = np.mgrid[0:size, 0:size] / 10.0 + 300.0
    ccd_data.data = ccd_data.data + yscan
    ccd_data = subtract_overscan(ccd_data, overscan=ccd_data[:, 0:10],
                                 median=False, model=models.Polynomial1D(2))
    assert abs(ccd_data.data.mean()) < 0.1


def test_subtract_overscan_fails(ccd_data):
    # do we get an error if the *image* is neither CCDData nor an array?
    with pytest.raises(TypeError):
        subtract_overscan(3, np.zeros((5, 5)))
    # do we get an error if the *overscan* is not an image or an array?
    with pytest.raises(TypeError):
        subtract_overscan(np.zeros((10, 10)), 3, median=False, model=None)
    # Do we get an error if we specify both overscan and fits_section?
    with pytest.raises(TypeError):
        subtract_overscan(ccd_data, overscan=ccd_data[0:10],
                          fits_section='[1:10]')
    # do we raise an error if we specify neither overscan nor fits_section?
    with pytest.raises(TypeError):
        subtract_overscan(ccd_data)
    # Does a fits_section which is not a string raise an error?
    with pytest.raises(TypeError):
        subtract_overscan(ccd_data, fits_section=5)


def test_trim_image_fits_section_requires_string(ccd_data):
    with pytest.raises(TypeError):
        trim_image(ccd_data, fits_section=5)


@pytest.mark.data_size(50)
def test_trim_image_fits_section(ccd_data):
    trimmed = trim_image(ccd_data, fits_section='[20:40,:]')
    # FITS reverse order, bounds are inclusive and starting index is 1-based
    assert trimmed.shape == (50, 21)
    np.testing.assert_array_equal(trimmed.data, ccd_data[:, 19:40])


@pytest.mark.data_size(50)
def test_trim_image_no_section(ccd_data):
    trimmed = trim_image(ccd_data[:, 19:40])
    assert trimmed.shape == (50, 21)
    np.testing.assert_array_equal(trimmed.data, ccd_data[:, 19:40])


def test_subtract_bias(ccd_data):
    data_avg = ccd_data.data.mean()
    bias_level = 5.0
    ccd_data.data = ccd_data.data + bias_level
    ccd_data.header['key'] = 'value'
    master_bias_array = np.zeros_like(ccd_data.data) + bias_level
    master_bias = CCDData(master_bias_array, unit=ccd_data.unit)
    no_bias = subtract_bias(ccd_data, master_bias)
    # Does the data we are left with have the correct average?
    np.testing.assert_almost_equal(no_bias.data.mean(), data_avg)
    # The test below is *NOT* really the desired outcome. Just here to make
    # sure a real test gets added when something is done with the metadata.
    assert no_bias.header == ccd_data.header
    del no_bias.header['key']
    assert len(ccd_data.header) > 0
    assert no_bias.header is not ccd_data.header


@pytest.mark.data_size(50)
def test_subtract_bias_fails(ccd_data):
    # Should fail if shapes don't match
    bias = CCDData(np.array([200, 200]), unit=u.adu)
    with pytest.raises(ValueError):
        subtract_bias(ccd_data, bias)
    # should fail because units don't match
    bias = CCDData(np.zeros_like(ccd_data), unit=u.meter)
    with pytest.raises(ValueError):
        subtract_bias(ccd_data, bias)


@pytest.mark.parametrize('exposure_keyword', [True, False])
@pytest.mark.parametrize('explicit_times', [True, False])
@pytest.mark.parametrize('scale', [True, False])
def test_subtract_dark(ccd_data, explicit_times, scale, exposure_keyword):
    exptime = 30.0
    exptime_key = 'exposure'
    dark_level = 1.7
    master_dark_data = np.zeros_like(ccd_data.data) + dark_level
    master_dark = CCDData(data=master_dark_data, unit=u.adu)
    master_dark.header[exptime_key] = 2 * exptime
    dark_exptime = master_dark.header[exptime_key]
    ccd_data.header[exptime_key] = exptime
    if explicit_times:
        dark_sub = subtract_dark(ccd_data, master_dark,
                                 dark_exposure=dark_exptime * u.second,
                                 data_exposure=exptime * u.second,
                                 scale=scale)
    elif exposure_keyword:
        key = Keyword(exptime_key, unit=u.second)
        dark_sub = subtract_dark(ccd_data, master_dark,
                                 exposure_time=key,
                                 scale=scale)
    else:
        dark_sub = subtract_dark(ccd_data, master_dark,
                                 exposure_time=exptime_key,
                                 exposure_unit=u.second,
                                 scale=scale)

    dark_scale = 1.0
    if scale:
        dark_scale = exptime / dark_exptime

    np.testing.assert_array_equal(ccd_data.data - dark_scale * dark_level,
                                  dark_sub.data)
    # Headers should have the same content...do they?
    assert dark_sub.header == ccd_data.header
    # But the headers should not be the same object -- a copy was made
    assert dark_sub.header is not ccd_data.header


def test_subtract_dark_fails(ccd_data):
    # None of these tests check a result so the content of the master
    # can be anything.
    ccd_data.header['exptime'] = 30.0
    master = ccd_data.copy()
    # Do we fail if we give one of dark_exposure, data_exposure but not both?
    with pytest.raises(TypeError):
        subtract_dark(ccd_data, master, dark_exposure=30 * u.second)
    with pytest.raises(TypeError):
        subtract_dark(ccd_data, master, data_exposure=30 * u.second)
    # Do we fail if we supply dark_exposure and data_exposure and exposure_time
    with pytest.raises(TypeError):
        subtract_dark(ccd_data, master, dark_exposure=10 * u.second,
                      data_exposure=10 * u.second,
                      exposure_time='exptime')
    # Fail if we supply none of the exposure-related arguments?
    with pytest.raises(TypeError):
        subtract_dark(ccd_data, master)
    # Fail if we supply exposure time but not a unit?
    with pytest.raises(TypeError):
        subtract_dark(ccd_data, master, exposure_time='exptime')
    # Fail if ccd_data or master are not CCDData objects?
    with pytest.raises(TypeError):
        subtract_dark(ccd_data.data, master, exposure_time='exptime')
    with pytest.raises(TypeError):
        subtract_dark(ccd_data, master.data, exposure_time='exptime')


# test for flat correction
@pytest.mark.data_scale(10)
def test_flat_correct(ccd_data):
    size = ccd_data.shape[0]

    # create the flat
    data = 2 * np.ones((size, size))
    flat = CCDData(data, meta=fits.header.Header(), unit=ccd_data.unit)
    ccd_data = flat_correct(ccd_data, flat)


# test for variance and for flat correction


@pytest.mark.data_scale(10)
@pytest.mark.data_mean(300)
def test_flat_correct_variance(ccd_data):
    size = ccd_data.shape[0]
    ccd_data.unit = electron
    ccd_data = create_variance(ccd_data, readnoise=5 * electron)
    # create the flat
    data = 2 * np.ones((size, size))
    flat = CCDData(data, meta=fits.header.Header(), unit=ccd_data.unit)
    flat = create_variance(flat, readnoise=0.5 * electron)
    ccd_data = flat_correct(ccd_data, flat)


# tests for gain correction
def test_gain_correct(ccd_data):
    init_data = ccd_data.data
    ccd_data = gain_correct(ccd_data, gain=3)
    assert_array_equal(ccd_data.data, 3 * init_data)


def test_gain_correct_quantity(ccd_data):
    init_data = ccd_data.data
    g = Quantity(3, electron / u.adu)
    ccd_data = gain_correct(ccd_data, gain=g)

    assert_array_equal(ccd_data.data, 3 * init_data)
    assert ccd_data.unit == electron
