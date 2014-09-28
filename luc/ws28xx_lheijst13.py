#!/usr/bin/python
# $Id$
#
# Copyright 2013 Matthew Wall
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or any later version.
#
# This parogram is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.
#
# See http://www.gnu.org/licenses/
#
# Thanks to Eddie De Pieri for the first Python implementation for WS-28xx.
# Eddie did the difficult work of decompiling HeavyWeather then converting
# and reverse engineering into a functional Python implementation.  Eddie's
# work was based on reverse engineering of HeavyWeather 2800 v 1.54
#
# Modifications by Luc Heijst:
# Naming conventions
# USBHardware, CCurrentWeatherData, CWeatherStationConfig, CHistoryDataSet, buildConfigFrame, generateResponse  

"""Classes and functions for interfacing with WS-28xx weather stations.

LaCrosse makes a number of stations in the 28xx series, including:

  WS-2810, WS-2810U-IT
  WS-2811, WS-2811SAL-IT,  WS-2811BRN-IT,  WS-2811OAK-IT
  WS-2812, WS-2812U-IT
  WS-2813
  WS-2814, WS-2814U-IT
  WS-2815, WS-2815U-IT
  C86234

The station is also sold as the TFA Primus and TechnoLine.

HeavyWeather is the software provided by LaCrosse.

There are two versions of HeavyWeather for the WS-28xx series: 1.5.4 and 1.5.4b
Apparently there is a difference between TX59UN-1-IT and TX59U-IT models (this
identifier is printed on the thermo-hygro sensor).

   HeavyWeather Version    Firmware Version    Thermo-Hygro Model
   1.54                    333 or 332          TX59UN-1-IT
   1.54b                   288, 262, 222       TX59U-IT

HeavyWeather provides the following weather station settings:
  time display: 12|24 hour
  temperature display: C|F
  air pressure display: inhg|hpa
  wind speed display: m/s|knos|bft|km/h|mph
  rain display: mm|inch
  recording interval: 1m
  keep weather station in hi-speed communication mode: true/false

According to the HeavyWeatherPro User Manual (1.54, rev2), "Hi speed mode wears
down batteries on your display much faster, and similarly consumes more power
on the PC.  We do not believe most users need to enable this setting.  It was
provided at the request of users who prefer ultra-frequent uploads."

The HeavyWeatherPro 'CurrentWeather' view is updated as data arrive from the
console.  The consonle sends current weather data approximately every 13
seconds.

Historical data are updated less frequently - every 2 hours in the default
HeavyWeatherPro configuration.

According to the User Manual, "The 2800 series weather station uses the
'original' wind chill calculation rather than the 2001 'North American'
formula because the original formula is international."

Apparently the station console determines when data will be sent, and, once
paired, the transceiver is always listening.  The station console sends a
broadcast on the hour.  If the transceiver responds, the station console may
continue to broad castdata, depending on the transceiver response and the
timing of the transceiver response.

According to the C86234 Operations Manual (Revision 7):
 - Temperature and humidity data are sent to the console every 13 seconds.
 - Wind data are sent to the temperature/humidity sensor every 17 seconds.
 - Rain data are sent to the temperature/humidity sensor every 19 seconds.
 - Air pressure is measured every 15 seconds.
"""

# TODO: how often is currdat.lst modified with/without hi-speed mode?

from datetime import datetime
from datetime import timedelta
from configobj import ConfigObj

import copy
import math
import platform
import syslog
import threading
import time
import traceback
import usb

import weeutil.weeutil
import weewx.abstractstation
import weewx.units

TMPCFG = '/tmp/ws28xx.tmp'
CFGFILE = '/tmp/ws28xx.cfg'

def logdbg(msg):
    # syslog.syslog(syslog.LOG_DEBUG, 'ws28xx: %s' % msg)
    return

def loginf(msg):
    syslog.syslog(syslog.LOG_INFO, 'ws28xx: %s' % msg)

def logcrt(msg):
    syslog.syslog(syslog.LOG_CRIT, 'ws28xx: %s' % msg)

def logerr(msg):
    syslog.syslog(syslog.LOG_ERR, 'ws28xx: %s' % msg)

# noaa definitions for station pressure, altimeter setting, and sea level
# http://www.crh.noaa.gov/bou/awebphp/definitions_pressure.php

# FIXME: this goes in wxformulas
# implementation copied from wview
def sp2ap(sp_mbar, elev_meter):
    """Convert station pressure to sea level pressure.
    http://www.wrh.noaa.gov/slc/projects/wxcalc/formulas/altimeterSetting.pdf

    sp_mbar - station pressure in millibars

    elev_meter - station elevation in meters

    ap - sea level pressure (altimeter) in millibars
    """

    if sp_mbar is None or elev_meter is None:
        return None
    N = 0.190284
    slp = 1013.25
    ct = (slp ** N) * 0.0065 / 288
    vt = elev_meter / ((sp_mbar - 0.3) ** N)
    ap_mbar = (sp_mbar - 0.3) * ((ct * vt + 1) ** (1/N))
    return ap_mbar

# FIXME: this goes in wxformulas
# implementation copied from wview
def sp2bp(sp_mbar, elev_meter, t_C):
    """Convert station pressure to sea level pressure.

    sp_mbar - station pressure in millibars

    elev_meter - station elevation in meters

    t_C - temperature in degrees Celsius

    bp - sea level pressure (barometer) in millibars
    """

    if sp_mbar is None or elev_meter is None or t_C is None:
        return None
    t_K = t_C + 273.15
    pt = math.exp( - elev_meter / (t_K * 29.263))
    bp_mbar = sp_mbar / pt if pt != 0 else 0
    return bp_mbar

# FIXME: this goes in weeutil.weeutil or weewx.units
def getaltitudeM(config_dict):
    # The driver needs the altitude in meters in order to calculate relative
    # pressure. Get it from the Station data and do any necessary conversions.
    altitude_t = weeutil.weeutil.option_as_list(
        config_dict['Station'].get('altitude', (None, None)))
    altitude_vt = (float(altitude_t[0]), altitude_t[1], "group_altitude")
    altitude_m = weewx.units.convert(altitude_vt, 'meter')[0]
    return altitude_m

# FIXME: this goes in weeutil.weeutil
# let QC handle rainfall that is too big
def calculate_rain(newtotal, oldtotal, maxsane=2):
    """Calculate the rain differential given two cumulative measurements."""
    if newtotal is not None and oldtotal is not None:
        if newtotal >= oldtotal:
            delta = newtotal - oldtotal
        else:  # wraparound
            logerr('rain counter wraparound detected: new: %s old: %s' % (newtotal, oldtotal))
            delta = None
    else:
        delta = None
    return delta

def loader(config_dict, engine):
    altitude_m = getaltitudeM(config_dict)
    station = WS28xx(altitude=altitude_m, **config_dict['WS28xx'])
    return station

class WS28xx(weewx.abstractstation.AbstractStation):
    """Driver for LaCrosse WS28xx stations."""
    
    def __init__(self, **stn_dict) :
        logdbg('WS28xx_init')
        """Initialize the station object.

        altitude: Altitude of the station
        [Required. No default]

        pressure_offset: Calibration offset in millibars for the station
        pressure sensor.  This offset is added to the station sensor output
        before barometer and altimeter pressures are calculated.
        [Optional. No Default]

        model: Which station model is this?
        [Optional. Default is 'LaCrosse WS28xx']

        transceiver_frequency: Frequency for transceiver-to-console.  Specify
        either US or EURO.
        [Required. Default is US]

        polling_interval: How often to sample the USB interface for data.
        [Optional. Default is 30 seconds]

        vendor_id: The USB vendor ID for the transceiver.
        [Optional. Default is 6666]

        product_id: The USB product ID for the transceiver.
        [Optional. Default is 5555]
        """

        self.altitude          = stn_dict['altitude']
        self.model             = stn_dict.get('model', 'LaCrosse WS28xx')
        self.cfgfile           = '/tmp/ws28xx.cfg'
        self.polling_interval  = int(stn_dict.get('polling_interval', 30))
        self.frequency         = stn_dict.get('transceiver_frequency', 'US')
        self.vendor_id         = int(stn_dict.get('vendor_id',  '0x6666'), 0)
        self.product_id        = int(stn_dict.get('product_id', '0x5555'), 0)
        self.pressure_offset   = stn_dict.get('pressure_offset', None)
        if self.pressure_offset is not None:
            self.pressure_offset = float(self.pressure_offset)

        self._service = None
        self._last_rain = None
        self._last_obs_ts = None

        loginf('frequency is %s' % self.frequency)
        loginf('altitude is %s meters' % str(self.altitude))
        loginf('pressure offset is %s' % str(self.pressure_offset))

    @property
    def hardware_name(self):
        return self.model

    def openPort(self):
        # FIXME: init the usb here
        pass

    def closePort(self):
        # FIXME: shutdown the usb port here
        pass

    def genLoopPackets(self):
        """Generator function that continuously returns decoded packets"""

        self.startup()

        maxnodata = 4
        nodata = 0
        while True:
            try:
                packet = self.get_observation()
                if packet is not None:
                    yield packet
                    nodata = 0
                else:
                    nodata += 1
                if nodata >= maxnodata:
                    dur = nodata * self.polling_interval
                    logerr('no new data after %d seconds' % dur)
                    nodata = 0

                time.sleep(self.polling_interval)
            except KeyboardInterrupt:
                self.shutdown()
                raise
            except Exception, e:
                logdbg('exception in genLoopPackets: BreakLoop')
                #traceback.print_exc() #lh no traceback needed; the exception is planned
                #self.shutdown() #lh Do not stop (and restart) RF communication
                raise

    def startup(self):
        if self._service is not None:
            return

        logdbg('Initialize communication service')
        self._service = CCommunicationService(self.cfgfile)
        self._service.DataStore.setCommModeInterval(5) #lh was: 3
        if self.frequency == 'EURO' or self.frequency == 'EU':
            self._service.DataStore.setTransmissionFrequency(1)
        else:
            self._service.DataStore.setTransmissionFrequency(0)

        self._service.startRFThread()
        self.check_transceiver()
        self._service.DataStore.setDeviceRegistered(True) #hack

    def shutdown(self):
        self._service.stopRFThread()
        self._service = None

    def check_transceiver(self, msg_to_console=False):
        maxtries = 12
        ntries = 0
        while ntries < maxtries:
            ntries += 1
            t = self._service.DataStore.getFlag_FLAG_TRANSCEIVER_PRESENT()
            msg = 'transceiver check: TC flag=%s (attempt %d of %d)' % (
                t, ntries, maxtries)
            if msg_to_console:
                print msg
            loginf(msg)
            if t:
                return
            time.sleep(5)
        else:
            raise Exception('Transceiver not responding.')

    def get_datum_diff(self, v, np):
        if abs(np - v) > 0.001:
            return v
        return None

    def get_datum_match(self, v, np):
        if np != v:
            return v
        return None

    def get_observation(self):
        logdbg('get_observation')
        ts = self._service.DataStore.CurrentWeather._timestamp
        if ts is None:
            return None
        if self._last_obs_ts is not None and self._last_obs_ts == ts:
            return None
        self._last_obs_ts = ts

        # add elements required for weewx LOOP packets
        packet = {}
        packet['usUnits'] = weewx.METRIC
        packet['dateTime'] = int(ts + 0.5)

        # data from the station sensors
        packet['inTemp'] = self.get_datum_diff(
            self._service.DataStore.CurrentWeather._TempIndoor,
            CWeatherTraits.TemperatureNP())
        packet['inHumidity'] = self.get_datum_diff(
            self._service.DataStore.CurrentWeather._HumidityIndoor,
            CWeatherTraits.HumidityNP())
        packet['outTemp'] = self.get_datum_diff(
            self._service.DataStore.CurrentWeather._TempOutdoor,
            CWeatherTraits.TemperatureNP())
        packet['outHumidity'] = self.get_datum_diff(
            self._service.DataStore.CurrentWeather._HumidityOutdoor,
            CWeatherTraits.HumidityNP())
        packet['pressure'] = self.get_datum_diff(
            self._service.DataStore.CurrentWeather._PressureRelative_hPa,
            CWeatherTraits.PressureNP())
        packet['windSpeed'] = self.get_datum_diff(
            self._service.DataStore.CurrentWeather._WindSpeed,
            CWeatherTraits.WindNP())
        packet['windGust'] = self.get_datum_diff(
            self._service.DataStore.CurrentWeather._Gust,
            CWeatherTraits.WindNP())

        if packet['windSpeed'] is not None:
            packet['windDir'] = self._service.DataStore.CurrentWeather._WindDirection * 360 / 16
        else:
            packet['windDir'] = None

        if packet['windGust'] is not None:
            packet['windGustDir'] = self._service.DataStore.CurrentWeather._GustDirection * 360 / 16
        else:
            packet['windGustDir'] = None

        # calculated elements not directly reported by station
        packet['rainRate'] = self.get_datum_match(
            self._service.DataStore.CurrentWeather._Rain1H,
            CWeatherTraits.RainNP())
        if packet['rainRate'] is not None:
            packet['rainRate'] /= 10 # weewx wants cm/hr
        rain_total = self.get_datum_match(
            self._service.DataStore.CurrentWeather._RainTotal,
            CWeatherTraits.RainNP())
        delta = calculate_rain(rain_total, self._last_rain)
        packet['rain'] = delta
        if packet['rain'] is not None:
            packet['rain'] /= 10 # weewx wants cm/hr
        self._last_rain = rain_total

        packet['heatindex'] = weewx.wxformulas.heatindexC(
            packet['outTemp'], packet['outHumidity'])
        packet['dewpoint'] = weewx.wxformulas.dewpointC(
            packet['outTemp'], packet['outHumidity'])
        packet['windchill'] = weewx.wxformulas.windchillC(
            packet['outTemp'], packet['windSpeed'])

        # station reports gauge pressure, must calculate other pressures
        adjp = packet['pressure']
        if self.pressure_offset is not None and adjp is not None:
            adjp += self.pressure_offset
        ###packet['barometer'] = adjp
        packet['barometer'] = sp2bp(adjp, self.altitude, packet['outTemp'])
        ###packet['barometer'] = sp2bp(adjp, self.altitude, packet['outTemp'] - packet['inTemp']) # correct for inTemp correction of wireless display
        packet['altimeter'] = sp2ap(adjp, self.altitude)

        return packet

    def get_config(self):
        logdbg('get station configuration')
        self._service.DataStore.getConfig()

# The following classes and methods are adapted from the implementation by
# eddie de pieri, which is in turn based on the HeavyWeather implementation.

def frame2str(n, buf):
    strbuf = ''
    for i in xrange(0,n):
        strbuf += str('%.2x' % buf[i])
    return strbuf

class BitHandling:
    # return a nonzero result, 2**offset, if the bit at 'offset' is one.
    @staticmethod
    def testBit(int_type, offset):
        mask = 1 << offset
        return(int_type & mask)

    # return an integer with the bit at 'offset' set to 1.
    @staticmethod
    def setBit(int_type, offset):
        mask = 1 << offset
        return(int_type | mask)

    # return an integer with the bit at 'offset' set to 1.
    @staticmethod
    def setBitVal(int_type, offset, val):
        mask = val << offset
        return(int_type | mask)

    # return an integer with the bit at 'offset' cleared.
    @staticmethod
    def clearBit(int_type, offset):
        mask = ~(1 << offset)
        return(int_type & mask)

    # return an integer with the bit at 'offset' inverted, 0->1 and 1->0.
    @staticmethod
    def toggleBit(int_type, offset):
        mask = 1 << offset
        return(int_type ^ mask)

class EHistoryInterval:
    hi01Min          = 0
    hi05Min          = 1
    hi10Min          = 2
    hi15Min          = 3
    hi20Min          = 4
    hi30Min          = 5
    hi60Min          = 6
    hi02Std          = 7
    hi04Std          = 8
    hi06Std          = 9
    hi08Std          = 0xA
    hi12Std          = 0xB
    hi24Std          = 0xC

class EWindspeedFormat:
    wfMs             = 0
    wfKnots          = 1
    wfBFT            = 2
    wfKmh            = 3
    wfMph            = 4

class ERainFormat:
    rfMm             = 0
    rfInch           = 1

class EPressureFormat:
    pfinHg           = 0
    pfHPa            = 1

class ETemperatureFormat:
    tfFahrenheit     = 0
    tfCelsius        = 1

class EClockMode:
    ct24H            = 0
    ctAmPm           = 1

class EWeatherTendency:
    TREND_NEUTRAL    = 0
    TREND_UP         = 1
    TREND_DOWN       = 2
    TREND_ERR        = 3

class EWeatherState:
    WEATHER_BAD      = 0
    WEATHER_NEUTRAL  = 1
    WEATHER_GOOD     = 2
    WEATHER_ERR      = 3

class EWindDirection:
    wdN              = 0
    wdNNE            = 1
    wdNE             = 2
    wdENE            = 3
    wdE              = 4
    wdESE            = 5
    wdSE             = 6
    wdSSE            = 7
    wdS              = 8
    wdSSW            = 9
    wdSW             = 0x0A
    wdWSW            = 0x0B
    wdW              = 0x0C
    wdWNW            = 0x0D
    wdNW             = 0x0E
    wdNNW            = 0x0F
    wdERR            = 0x10
    wdInvalid        = 0x11

class EResetMinMaxFlags:
    rmTempIndoorHi   = 0
    rmTempIndoorLo   = 1
    rmTempOutdoorHi  = 2
    rmTempOutdoorLo  = 3
    rmWindchillHi    = 4
    rmWindchillLo    = 5
    rmDewpointHi     = 6
    rmDewpointLo     = 7
    rmHumidityIndoorLo  = 8
    rmHumidityIndoorHi  = 9
    rmHumidityOutdoorLo  = 0x0A
    rmHumidityOutdoorHi  = 0x0B
    rmWindspeedHi    = 0x0C
    rmWindspeedLo    = 0x0D
    rmGustHi         = 0x0E
    rmGustLo         = 0x0F
    rmPressureLo     = 0x10
    rmPressureHi     = 0x11
    rmRain1hHi       = 0x12
    rmRain24hHi      = 0x13
    rmRainLastWeekHi  = 0x14
    rmRainLastMonthHi  = 0x15
    rmRainTotal      = 0x16
    rmInvalid        = 0x17

class ERequestType:
    rtGetCurrent     = 0
    rtGetHistory     = 1
    rtGetConfig      = 2
    rtSetConfig      = 3
    rtSetTime        = 4
    rtINVALID        = 6

class ERequestState:
    rsQueued         = 0
    rsRunning        = 1
    rsFinished       = 2
    rsPreamble       = 3
    rsWaitDevice     = 4
    rsWaitConfig     = 5
    rsError          = 6
    rsChanged        = 7
    rsINVALID        = 8

class ETransmissionFrequency:
    tfUS             = 0
    tfEuropean       = 1
    tfUSFreq         = 905000000
    tfEuropeanFreq   = 868300000

class CWeatherTraits(object):
    windDirMap = {
        0:"N", 1:"NNE", 2:"NE", 3:"ENE", 4:"E", 5:"ESE", 6:"SE", 7:"SSE",
        8:"S", 9:"SSW", 10:"SW", 11:"WSW", 12:"W", 13:"WNW", 14:"NW",
        15:"NWN", 16:"err", 17:"inv" }
    forecastMap = {
        0:"Rainy(Bad)", 1:"Cloudy(Neutral)", 2:"Sunny(Good)",  3:"Error" }
    trends = {
        0:"Stable(Neutral)", 1:"Rising(Up)", 2:"Falling(Down)", 3:"Error" }

    @staticmethod
    def TemperatureNP():
        return 81.099998

    @staticmethod
    def TemperatureOFL():
        return 136.0

    @staticmethod
    def PressureNP():
        return 10101010.0

    @staticmethod
    def PressureOFL():
        return 16666.5

    @staticmethod
    def HumidityNP():
        return 110.0

    @staticmethod
    def HumidityOFL():
        return 121.0

    @staticmethod
    def RainNP():
        return -0.2

    @staticmethod
    def RainOFL():
        return 16666.664

    @staticmethod
    def WindNP():
        return 51.0

    @staticmethod
    def WindOFL():
        return 51.099998

    @staticmethod
    def TemperatureOffset():
        return 40.0

class CMeasurement:
    _Value = 0.0
    _ResetFlag = 23
    _IsError = 1
    _IsOverflow = 1
    _Time = time.time()

    def Reset(self):
        self._Value = 0.0
        self._ResetFlag = 23
        self._IsError = 1
        self._IsOverflow = 1

class CMinMaxMeasurement(object):
    def __init__(self):
        self._Min = CMeasurement()
        self._Max = CMeasurement()

class USBHardware(object):
    @staticmethod
    def isOFL2(buf, start, StartOnHiNibble):
        if StartOnHiNibble :
            result =   (buf[0][start+0] >>  4) == 15 \
                or (buf[0][start+0] & 0xF) == 15
        else:
            result =   (buf[0][start+0] & 0xF) == 15 \
                or (buf[0][start+1] >>  4) == 15
        return result

    @staticmethod
    def isOFL3(buf, start, StartOnHiNibble):
        if StartOnHiNibble :
            result =   (buf[0][start+0] >>  4) == 15 \
                or (buf[0][start+0] & 0xF) == 15 \
                or (buf[0][start+1] >>  4) == 15
        else:
            result =   (buf[0][start+0] & 0xF) == 15 \
                or (buf[0][start+1] >>  4) == 15 \
                or (buf[0][start+1] & 0xF) == 15
        return result

    @staticmethod
    def isOFL5(buf, start, StartOnHiNibble):
        if StartOnHiNibble :
            result =     (buf[0][start+0] >>  4) == 15 \
                or (buf[0][start+0] & 0xF) == 15 \
                or (buf[0][start+1] >>  4) == 15 \
                or (buf[0][start+1] & 0xF) == 15 \
                or (buf[0][start+2] >>  4) == 15
        else:
            result =     (buf[0][start+0] & 0xF) == 15 \
                or (buf[0][start+1] >>  4) == 15 \
                or (buf[0][start+1] & 0xF) == 15 \
                or (buf[0][start+2] >>  4) == 15 \
                or (buf[0][start+2] & 0xF) == 15
        return result

    @staticmethod
    def isErr2(buf, start, StartOnHiNibble):
        if StartOnHiNibble :
            result =    (buf[0][start+0] >>  4) >= 10 \
                and (buf[0][start+0] >>  4) != 15 \
                or  (buf[0][start+0] & 0xF) >= 10 \
                and (buf[0][start+0] & 0xF) != 15
        else:
            result =    (buf[0][start+0] & 0xF) >= 10 \
                and (buf[0][start+0] & 0xF) != 15 \
                or  (buf[0][start+1] >>  4) >= 10 \
                and (buf[0][start+1] >>  4) != 15
        return result
        
    @staticmethod
    def isErr3(buf, start, StartOnHiNibble):
        if StartOnHiNibble :
            result =     (buf[0][start+0] >>  4) >= 10 \
                and (buf[0][start+0] >>  4) != 15 \
                or  (buf[0][start+0] & 0xF) >= 10 \
                and (buf[0][start+0] & 0xF) != 15 \
                or  (buf[0][start+1] >>  4) >= 10 \
                and (buf[0][start+1] >>  4) != 15
        else:
            result =     (buf[0][start+0] & 0xF) >= 10 \
                and (buf[0][start+0] & 0xF) != 15 \
                or  (buf[0][start+1] >>  4) >= 10 \
                and (buf[0][start+1] >>  4) != 15 \
                or  (buf[0][start+1] & 0xF) >= 10 \
                and (buf[0][start+1] & 0xF) != 15
        return result
        
    @staticmethod
    def isErr5(buf, start, StartOnHiNibble):
        if StartOnHiNibble :
            result =     (buf[0][start+0] >>  4) >= 10 \
                and (buf[0][start+0] >>  4) != 15 \
                or  (buf[0][start+0] & 0xF) >= 10 \
                and (buf[0][start+0] & 0xF) != 15 \
                or  (buf[0][start+1] >>  4) >= 10 \
                and (buf[0][start+1] >>  4) != 15 \
                or  (buf[0][start+1] & 0xF) >= 10 \
                and (buf[0][start+1] & 0xF) != 15 \
                or  (buf[0][start+2] >>  4) >= 10 \
                and (buf[0][start+2] >>  4) != 15
        else:
            result =     (buf[0][start+0] & 0xF) >= 10 \
                and (buf[0][start+0] & 0xF) != 15 \
                or  (buf[0][start+1] >>  4) >= 10 \
                and (buf[0][start+1] >>  4) != 15 \
                or  (buf[0][start+1] & 0xF) >= 10 \
                and (buf[0][start+1] & 0xF) != 15 \
                or  (buf[0][start+2] >>  4) >= 10 \
                and (buf[0][start+2] >>  4) != 15 \
                or  (buf[0][start+2] & 0xF) >= 10 \
                and (buf[0][start+2] & 0xF) != 15
        return result

    @staticmethod
    def reverseByteOrder(buf, start, Count):
        nbuf=buf[0]
        for i in xrange(0, Count >> 1):
            tmp = nbuf[start + i]
            nbuf[start + i] = nbuf[start + Count - i - 1]
            nbuf[start + Count - i - 1 ] = tmp
        buf[0]=nbuf

    @staticmethod
    def readWindDirectionShared(buf, start):
        return (buf[0][0+start] & 0xF, buf[0][start] >> 4)

    @staticmethod
    def toInt_2(buf, start, StartOnHiNibble): # read 2 nibbles
        if StartOnHiNibble:
            rawpre  = (buf[0][start+0] >>  4)* 10 \
                + (buf[0][start+0] & 0xF)* 1
        else:
            rawpre  = (buf[0][start+0] & 0xF)* 10 \
                + (buf[0][start+1] >>  4)* 1
        return rawpre

    @staticmethod
    def toRain_7_3(buf, start, StartOnHiNibble): #read 7 nibbles, presentation with 3 decimals
        if ( USBHardware.isErr2(buf, start+0, StartOnHiNibble) or
            USBHardware.isErr5(buf, start+1, StartOnHiNibble)):
            result = CWeatherTraits.RainNP()
        elif ( USBHardware.isOFL2(buf, start+0, StartOnHiNibble) or
                USBHardware.isOFL5(buf, start+1, StartOnHiNibble) ):
            result = CWeatherTraits.RainOFL()
        elif StartOnHiNibble:
            result  = (buf[0][start+0] >>  4)*  1000 \
                + (buf[0][start+0] & 0xF)* 100    \
                + (buf[0][start+1] >>  4)*  10    \
                + (buf[0][start+1] & 0xF)*   1    \
                + (buf[0][start+2] >>  4)*   0.1  \
                + (buf[0][start+2] & 0xF)*   0.01 \
                + (buf[0][start+3] >>  4)*   0.001
        else:
            result  = (buf[0][start+0] & 0xF)*  1000 \
                + (buf[0][start+1] >>  4)* 100    \
                + (buf[0][start+1] & 0xF)*  10    \
                + (buf[0][start+2] >>  4)*   1    \
                + (buf[0][start+2] & 0xF)*   0.1  \
                + (buf[0][start+3] >>  4)*   0.01 \
                + (buf[0][start+3] & 0xF)*   0.001
        return result

    @staticmethod
    def toRain_6_2(buf, start, StartOnHiNibble): #read 6 nibbles, presentation with 2 decimals
        if ( USBHardware.isErr2(buf, start+0, StartOnHiNibble) or
                USBHardware.isErr2(buf, start+1, StartOnHiNibble) or
                USBHardware.isErr2(buf, start+2, StartOnHiNibble) ):
            result = CWeatherTraits.RainNP()
        elif ( USBHardware.isOFL2(buf, start+0, StartOnHiNibble) or
                USBHardware.isOFL2(buf, start+1, StartOnHiNibble) or
                USBHardware.isOFL2(buf, start+2, StartOnHiNibble) ):
            result = CWeatherTraits.RainOFL()
        elif StartOnHiNibble:
            result  = (buf[0][start+0] >>  4)*  1000 \
                + (buf[0][start+0] & 0xF)* 100   \
                + (buf[0][start+1] >>  4)*  10   \
                + (buf[0][start+1] & 0xF)*   1   \
                + (buf[0][start+2] >>  4)*   0.1 \
                + (buf[0][start+2] & 0xF)*   0.01
        else:
            result  = (buf[0][start+0] & 0xF)*  1000 \
                + (buf[0][start+1] >>  4)* 100   \
                + (buf[0][start+1] & 0xF)*  10   \
                + (buf[0][start+2] >>  4)*   1   \
                + (buf[0][start+2] & 0xF)*   0.1 \
                + (buf[0][start+3] >>  4)*   0.01
        return result

    @staticmethod
    def toRain_3_1(buf, start, StartOnHiNibble): #read 3 nibbles, presentation with 1 decimal
        if StartOnHiNibble :
            hibyte = buf[0][start+0]
            lobyte = (buf[0][start+1] >> 4) & 0xF
        else:
            hibyte = 16*(buf[0][start+0] & 0xF) + ((buf[0][start+1] >> 4) & 0xF)
            lobyte = buf[0][start+1] & 0xF            
        if hibyte == 0xFF and lobyte == 0xE :
            result = CWeatherTraits.RainNP()
        elif hibyte == 0xFF and lobyte == 0xF :
            result = CWeatherTraits.RainOFL()
        else:
            val = USBHardware.toFloat_3_1(buf, start, StartOnHiNibble)
            result = val
        return result

    @staticmethod  
    def toFloat_3_1(buf, start, StartOnHiNibble): #read 3 nibbles, presentation with 1 decimal
        if StartOnHiNibble:
            result = (buf[0][start+0] >>  4)*16**2 \
                + (buf[0][start+0] & 0xF)*   16**1 \
                + (buf[0][start+1] >>  4)*   16**0
        else:
            result = (buf[0][start+0] & 0xF)*16**2 \
                + (buf[0][start+1] >>  4)*   16**1 \
                + (buf[0][start+1] & 0xF)*   16**0
        result = result / 10.0
        return result
    
    @staticmethod
    def toDateTime(buf, start, StartOnHiNibble): #read 10 nibbles, presentation as DateTime
        if ( USBHardware.isErr2(buf, start+0, StartOnHiNibble)
             or USBHardware.isErr2(buf, start+1, StartOnHiNibble)
             or USBHardware.isErr2(buf, start+2, StartOnHiNibble)
             or USBHardware.isErr2(buf, start+3, StartOnHiNibble)
             or USBHardware.isErr2(buf, start+4, StartOnHiNibble) ):
            # FIXME: use None instead of a really old date to indicate invalid
            logdbg('toDateTime: BOGUS DATE')
            result = datetime(1900, 01, 01, 00, 00)
        else:
            year    = USBHardware.toInt_2(buf, start+0, StartOnHiNibble) + 2000
            month   = USBHardware.toInt_2(buf, start+1, StartOnHiNibble)
            days    = USBHardware.toInt_2(buf, start+2, StartOnHiNibble)
            hours   = USBHardware.toInt_2(buf, start+3, StartOnHiNibble)
            minutes = USBHardware.toInt_2(buf, start+4, StartOnHiNibble)
            #lh check for illegal datetime format
            try:
                result = datetime(year, month, days, hours, minutes)
            except:
                logdbg('Error in date timeformat %4i-%2i-%2i %2i:%2i' % (year, month, days, hours, minutes))
                result = datetime(1900, 01, 01, 00, 00)
        return result

    @staticmethod
    def toHumidity_2_0(buf, start, StartOnHiNibble): #read 2 nibbles, presentation with 0 decimal
        if USBHardware.isErr2(buf, start+0, StartOnHiNibble) :
            result = CWeatherTraits.HumidityNP()
        elif USBHardware.isOFL2(buf, start+0, StartOnHiNibble) :
            result = CWeatherTraits.HumidityOFL()
        else:
            result = USBHardware.toInt_2(buf, start, StartOnHiNibble)
        return result

    @staticmethod
    def toTemperature_5_3(buf, start, StartOnHiNibble): #read 5 nibbles, presentation with 3 decimals
        if USBHardware.isErr5(buf, start+0, StartOnHiNibble) :
            result = CWeatherTraits.TemperatureNP()
        elif USBHardware.isOFL5(buf, start+0, StartOnHiNibble) :
            result = CWeatherTraits.TemperatureOFL()
        else:
            if StartOnHiNibble:
                rawtemp = (buf[0][start+0] >>  4)* 10 \
                    + (buf[0][start+0] & 0xF)*  1     \
                    + (buf[0][start+1] >>  4)*  0.1   \
                    + (buf[0][start+1] & 0xF)*  0.01  \
                    + (buf[0][start+2] >>  4)*  0.001
            else:
                rawtemp = (buf[0][start+0] & 0xF)* 10 \
                    + (buf[0][start+1] >>  4)*  1     \
                    + (buf[0][start+1] & 0xF)*  0.1   \
                    + (buf[0][start+2] >>  4)*  0.01  \
                    + (buf[0][start+2] & 0xF)*  0.001
            result = rawtemp - CWeatherTraits.TemperatureOffset()
        return result

    @staticmethod
    def toTemperature_3_1(buf, start, StartOnHiNibble): #read 3 nibbles, presentation with 1 decimal
        if USBHardware.isErr3(buf, start+0, StartOnHiNibble) :
            result = CWeatherTraits.TemperatureNP()
        elif USBHardware.isOFL3(buf, start+0, StartOnHiNibble) :
            result = CWeatherTraits.TemperatureOFL()
        else:
            if StartOnHiNibble :
                rawtemp   =  (buf[0][start+0] >>  4)*  10 \
                    +  (buf[0][start+0] & 0xF)*  1   \
                    +  (buf[0][start+1] >>  4)*  0.1
            else:
                rawtemp   =  (buf[0][start+0] & 0xF)*  10 \
                    +  (buf[0][start+1] >>  4)*  1   \
                    +  (buf[0][start+1] & 0xF)*  0.1 
            result = rawtemp - CWeatherTraits.TemperatureOffset()
        return result

    @staticmethod
    def toWindspeed_5_2(buf, start, StartOnHiNibble): #read 5 nibbles, presentation with 2 decimals
        if StartOnHiNibble:
            result = (buf[0][start+2] >> 4)* 16**6 \
                + (buf[0][start+0] >>  4)*   16**5 \
                + (buf[0][start+0] & 0xF)*   16**4 \
                + (buf[0][start+1] >>  4)*   16**3 \
                + (buf[0][start+1] & 0xF)*   16**2
        else:
            result = (buf[0][start+2] >> 4)* 16**6 \
                + (buf[0][start+2] & 0xF)*   16**5 \
                + (buf[0][start+0] >>  4)*   16**5 \
                + (buf[0][start+1] & 0xF)*   16**3 \
                + (buf[0][start+1] >>  4)*   16**2
        result = result / 256.0 / 100.0
        return result

    @staticmethod
    def toWindspeed_3_1(buf, start, StartOnHiNibble): #read 3 nibbles, presentation with 1 decimal
        if StartOnHiNibble :
            hibyte = buf[0][start+0]
            lobyte = (buf[0][start+1] >> 4) & 0xF
        else:
            hibyte = 16*(buf[0][start+0] & 0xF) + ((buf[0][start+1] >> 4) & 0xF)
            lobyte = buf[0][start+1] & 0xF            
        if hibyte == 0xFF and lobyte == 0xE :
            result = CWeatherTraits.WindNP()
        elif hibyte == 0xFF and lobyte == 0xF :
            result = CWeatherTraits.WindOFL()
        else:
            val = USBHardware.toFloat_3_1(buf, start, StartOnHiNibble)
            result = val
        return result

    @staticmethod
    def readPressureShared(buf, start, StartOnHiNibble):
        return ( USBHardware.toPressure_hPa_5_1(buf, start+2, 1-StartOnHiNibble) ,
                 USBHardware.toPressure_inHg_5_2(buf, start, StartOnHiNibble))

    @staticmethod
    def toPressure_hPa_5_1(buf, start, StartOnHiNibble): #read 5 nibbles, presentation with 1 decimal
        if USBHardware.isErr5(buf, start+0, StartOnHiNibble) :
            result = CWeatherTraits.PressureNP()
        elif USBHardware.isOFL5(buf, start+0, StartOnHiNibble) :
            result = CWeatherTraits.PressureOFL()
        elif StartOnHiNibble :
            result = (buf[0][start+0] >> 4)* 1000 \
                + (buf[0][start+0] & 0xF)* 100  \
                + (buf[0][start+1] >>  4)*  10  \
                + (buf[0][start+1] & 0xF)*  1   \
                + (buf[0][start+2] >>  4)*  0.1
        else:
            result = (buf[0][start+0] & 0xF)* 1000 \
                + (buf[0][start+1] >>  4)* 100  \
                + (buf[0][start+1] & 0xF)*  10  \
                + (buf[0][start+2] >>  4)*  1   \
                + (buf[0][start+2] & 0xF)*  0.1
        return result

    @staticmethod
    def toPressure_inHg_5_2(buf, start, StartOnHiNibble): #read 5 nibbles, presentation with 2 decimals
        if USBHardware.isErr5(buf, start+0, StartOnHiNibble) :
            result = CWeatherTraits.PressureNP()
        elif USBHardware.isOFL5(buf, start+0, StartOnHiNibble) :
            result = CWeatherTraits.PressureOFL()
        elif StartOnHiNibble :
            result = (buf[0][start+0] >> 4)* 100 \
                + (buf[0][start+0] & 0xF)* 10   \
                + (buf[0][start+1] >>  4)*  1   \
                + (buf[0][start+1] & 0xF)*  0.1 \
                + (buf[0][start+2] >>  4)*  0.01
        else:
            result = (buf[0][start+0] & 0xF)* 100 \
                + (buf[0][start+1] >>  4)* 10   \
                + (buf[0][start+1] & 0xF)*  1   \
                + (buf[0][start+2] >>  4)*  0.1 \
                + (buf[0][start+2] & 0xF)*  0.01
        return result

    @staticmethod
    def dumpBuf(cmd, buf, length):
        buflen = len(buf)
        end = min(buflen,length)
        pos = 1
        startnr = pos-1
        strbuf = str(' %.3d: ' % startnr)
        while pos <= end:
            strbuf += str('%.2x ' % buf[pos-1])
            if pos%10 == 0:
                strbuf += str(' ')
            if pos%30 == 0:
                loginf('%s %s' % (cmd,strbuf))
                startnr = pos    
                strbuf = str(' %.3d: ' % startnr)
            pos += 1
        if pos-1 != startnr:
            loginf('%s %s' % (cmd,strbuf))
            
    @staticmethod
    def dumpBufRev(cmd, buf, start, length):
        buflen = len(buf)
        end = min(buflen,length)
        pos = 1
        startnr = pos-1
        strbuf = str(' %.3d: ' % startnr)
        while pos <= end:
            strbuf += str('%.2x ' % buf[end-pos+start])
            if pos%10 == 0:
                strbuf += str(' ')
            if pos%30 == 0:
                loginf('Rev %s %s' % (cmd,strbuf))
                startnr = pos    
                strbuf = str(' %.3d: ' % startnr)
            pos += 1
        if pos-1 != startnr:
            loginf('Rev %s %s' % (cmd,strbuf))

class CCurrentWeatherData(object):

    def __init__(self):
        logdbg('CCurrentWeatherData_init')
        self._timestamp = None
        self._PressureRelative_hPa = CWeatherTraits.PressureNP()
        self._PressureRelative_hPaMinMax = CMinMaxMeasurement()
        self._PressureRelative_inHg = CWeatherTraits.PressureNP()
        self._PressureRelative_inHgMinMax = CMinMaxMeasurement()
        self._WindSpeed = CWeatherTraits.WindNP()
        self._WindDirection = 16
        self._WindDirection1 = 16
        self._WindDirection2 = 16
        self._WindDirection3 = 16
        self._WindDirection4 = 16
        self._WindDirection5 = 16
        self._Gust = CWeatherTraits.WindNP()
        self._GustMax = CMinMaxMeasurement()
        self._GustDirection = 16
        self._GustDirection1 = 16
        self._GustDirection2 = 16
        self._GustDirection3 = 16
        self._GustDirection4 = 16
        self._GustDirection5 = 16
        self._Rain1H = CWeatherTraits.RainNP()
        self._Rain1HMax = CMinMaxMeasurement()
        self._Rain24H = CWeatherTraits.RainNP()
        self._Rain24HMax = CMinMaxMeasurement()
        self._RainLastWeek = CWeatherTraits.RainNP()
        self._RainLastWeekMax = CMinMaxMeasurement()
        self._RainLastMonth = CWeatherTraits.RainNP()
        self._RainLastMonthMax = CMinMaxMeasurement()
        self._RainTotal = CWeatherTraits.RainNP()
        self._LastRainReset = time.time()
        self._TempIndoor = CWeatherTraits.TemperatureNP()
        self._TempIndoorMinMax = CMinMaxMeasurement()
        self._TempOutdoor = CWeatherTraits.TemperatureNP()
        self._TempOutdoorMinMax = CMinMaxMeasurement()
        self._HumidityIndoor = CWeatherTraits.HumidityNP()
        self._HumidityIndoorMinMax = CMinMaxMeasurement()
        self._HumidityOutdoor = CWeatherTraits.HumidityNP()
        self._HumidityOutdoorMinMax = CMinMaxMeasurement()
        self._Dewpoint = CWeatherTraits.TemperatureNP()
        self._DewpointMinMax = CMinMaxMeasurement()
        self._Windchill = CWeatherTraits.TemperatureNP()
        self._WindchillMinMax = CMinMaxMeasurement()
        self._WeatherState = 3
        self._WeatherTendency = 3
        self._AlarmRingingFlags = 0x0000
        self._AlarmMarkedFlags = 0
        self._PresRel_hPa_Max = 0.0
        self._PresRel_inHg_Max = 0.0
        
    def CCurrentWeatherData_buf(self,buf,pos):
        self._LastRainReset = time.time()
        self.readCurrentWeather(buf,pos);

    def readCurrentWeather(self,buf,pos):
        logdbg('readCurrentWeather')
        nbuf = [0]
        nbuf[0] = buf[0]
        ###USBHardware.dumpBuf('Cur ', nbuf[0], 0xd7) 
        self._StartBytes = nbuf[0][6]*0xF + nbuf[0][7]
        self._WeatherTendency = (nbuf[0][8] >> 4) & 0xF
        if self._WeatherTendency > 3:
            self._WeatherTendency = 3 
        self._WeatherState = nbuf[0][8] & 0xF
        if self._WeatherState > 3:
            self._WeatherState = 3 

        self._TempIndoorMinMax._Max._Value = USBHardware.toTemperature_5_3(nbuf, 19, 0)
        self._TempIndoorMinMax._Min._Value = USBHardware.toTemperature_5_3(nbuf, 22, 1)
        self._TempIndoor = USBHardware.toTemperature_5_3(nbuf, 24, 0)
        if self._TempIndoorMinMax._Min._Value == CWeatherTraits.TemperatureNP():
            self._TempIndoorMinMax._Min._IsError = 1
        else:
            self._TempIndoorMinMax._Min._IsError = 0
        if self._TempIndoorMinMax._Min._Value == CWeatherTraits.TemperatureOFL():
            self._TempIndoorMinMax._Min._IsOverflow = 1
        else:
            self._TempIndoorMinMax._Min._IsOverflow = 0
        if self._TempIndoorMinMax._Max._Value == CWeatherTraits.TemperatureNP():
            self._TempIndoorMinMax._Max._IsError = 1
        else:
            self._TempIndoorMinMax._Max._IsError = 0
        if self._TempIndoorMinMax._Max._Value == CWeatherTraits.TemperatureOFL():
            self._TempIndoorMinMax._Max._IsOverflow = 1
        else:
            self._TempIndoorMinMax._Max._IsOverflow = 0
        if self._TempIndoorMinMax._Max._IsError or self._TempIndoorMinMax._Max._IsOverflow:
            self._TempIndoorMinMax._Max._Time = None
        else:
            self._TempIndoorMinMax._Max._Time = USBHardware.toDateTime(nbuf, 9, 0); 
        if self._TempIndoorMinMax._Min._IsError or self._TempIndoorMinMax._Min._IsOverflow:
            self._TempIndoorMinMax._Min._Time = None
        else:
            self._TempIndoorMinMax._Min._Time = USBHardware.toDateTime(nbuf, 14, 0)

        self._TempOutdoorMinMax._Max._Value = USBHardware.toTemperature_5_3(nbuf, 37, 0)
        self._TempOutdoorMinMax._Min._Value = USBHardware.toTemperature_5_3(nbuf, 40, 1)
        self._TempOutdoor = USBHardware.toTemperature_5_3(nbuf, 42, 0)
        if self._TempOutdoorMinMax._Min._Value == CWeatherTraits.TemperatureNP():
            self._TempOutdoorMinMax._Min._IsError = 1
        else:
            self._TempOutdoorMinMax._Min._IsError = 0
        if self._TempOutdoorMinMax._Min._Value == CWeatherTraits.TemperatureOFL():
            self._TempOutdoorMinMax._Min._IsOverflow = 1
        else:
            self._TempOutdoorMinMax._Min._IsOverflow = 0
        if self._TempOutdoorMinMax._Max._Value == CWeatherTraits.TemperatureNP():
            self._TempOutdoorMinMax._Max._IsError = 1
        else:
            self._TempOutdoorMinMax._Max._IsError = 0
        if self._TempOutdoorMinMax._Max._Value == CWeatherTraits.TemperatureOFL():
            self._TempOutdoorMinMax._Max._IsOverflow = 1
        else:
            self._TempOutdoorMinMax._Max._IsOverflow = 0
        if self._TempOutdoorMinMax._Max._IsError or self._TempOutdoorMinMax._Max._IsOverflow:
            self._TempOutdoorMinMax._Max._Time = None
        else:
            self._TempOutdoorMinMax._Max._Time = USBHardware.toDateTime(nbuf, 27, 0)
        if self._TempOutdoorMinMax._Min._IsError or self._TempOutdoorMinMax._Min._IsOverflow:
            self._TempOutdoorMinMax._Min._Time = None
        else:
            self._TempOutdoorMinMax._Min._Time = USBHardware.toDateTime(nbuf, 32, 0)

        self._WindchillMinMax._Max._Value = USBHardware.toTemperature_5_3(nbuf, 55, 0)
        self._WindchillMinMax._Min._Value = USBHardware.toTemperature_5_3(nbuf, 58, 1)
        self._Windchill = USBHardware.toTemperature_5_3(nbuf, 60, 0)
        if self._WindchillMinMax._Min._Value == CWeatherTraits.TemperatureNP():
            self._WindchillMinMax._Min._IsError = 1
        else:
            self._WindchillMinMax._Min._IsError = 0
        if self._WindchillMinMax._Min._Value == CWeatherTraits.TemperatureOFL():
            self._WindchillMinMax._Min._IsOverflow = 1
        else:
            self._WindchillMinMax._Min._IsOverflow = 0
        
        if self._WindchillMinMax._Max._Value == CWeatherTraits.TemperatureNP():
            self._WindchillMinMax._Max._IsError = 1
        else:
            self._WindchillMinMax._Max._IsError = 0
        if self._WindchillMinMax._Max._Value == CWeatherTraits.TemperatureOFL():
            self._WindchillMinMax._Max._IsOverflow = 1
        else:
            self._WindchillMinMax._Max._IsOverflow = 0
        if self._WindchillMinMax._Max._IsError or self._WindchillMinMax._Max._IsOverflow:
            self._WindchillMinMax._Max._Time = None
        else:
            self._WindchillMinMax._Max._Time = USBHardware.toDateTime(nbuf, 45, 0)
        if self._WindchillMinMax._Min._IsError or self._WindchillMinMax._Min._IsOverflow:
            self._WindchillMinMax._Min._Time = None
        else:
            self._WindchillMinMax._Min._Time = USBHardware.toDateTime(nbuf, 50, 0)

        self._DewpointMinMax._Max._Value = USBHardware.toTemperature_5_3(nbuf, 73, 0)
        self._DewpointMinMax._Min._Value = USBHardware.toTemperature_5_3(nbuf, 76, 1)
        self._Dewpoint = USBHardware.toTemperature_5_3(nbuf, 78, 0)
        if self._DewpointMinMax._Min._Value == CWeatherTraits.TemperatureNP():
            self._DewpointMinMax._Min._IsError = 1
        else:
            self._DewpointMinMax._Min._IsError = 0
        if self._DewpointMinMax._Min._Value == CWeatherTraits.TemperatureOFL():
            self._DewpointMinMax._Min._IsOverflow = 1
        else:
            self._DewpointMinMax._Min._IsOverflow = 0
        if self._DewpointMinMax._Max._Value == CWeatherTraits.TemperatureNP():
            self._DewpointMinMax._Max._IsError = 1
        else:
            self._DewpointMinMax._Max._IsError = 0
        if self._DewpointMinMax._Max._Value == CWeatherTraits.TemperatureOFL():
            self._DewpointMinMax._Max._IsOverflow = 1
        else:
            self._DewpointMinMax._Max._IsOverflow = 0
        if self._DewpointMinMax._Min._IsError or self._DewpointMinMax._Min._IsOverflow:
            self._DewpointMinMax._Min._Time = None
        else:
            self._DewpointMinMax._Min._Time = USBHardware.toDateTime(nbuf, 68, 0);
        if self._DewpointMinMax._Max._IsError or self._DewpointMinMax._Max._IsOverflow:
            self._DewpointMinMax._Max._Time = None
        else:
            self._DewpointMinMax._Max._Time = USBHardware.toDateTime(nbuf, 63, 0) 

        self._HumidityIndoorMinMax._Max._Value = USBHardware.toHumidity_2_0(nbuf, 91, 1)
        self._HumidityIndoorMinMax._Min._Value = USBHardware.toHumidity_2_0(nbuf, 92, 1)
        self._HumidityIndoor = USBHardware.toHumidity_2_0(nbuf, 93, 1)
        if self._HumidityIndoorMinMax._Min._Value == CWeatherTraits.HumidityNP():
            self._HumidityIndoorMinMax._Min._IsError = 1
        else:
            self._HumidityIndoorMinMax._Min._IsError = 0
        if self._HumidityIndoorMinMax._Min._Value == CWeatherTraits.HumidityOFL():
            self._HumidityIndoorMinMax._Min._IsOverflow = 1
        else:
            self._HumidityIndoorMinMax._Min._IsOverflow = 0
        if self._HumidityIndoorMinMax._Max._Value == CWeatherTraits.HumidityNP():
            self._HumidityIndoorMinMax._Max._IsError = 1
        else:
            self._HumidityIndoorMinMax._Max._IsError = 0
        if self._HumidityIndoorMinMax._Max._Value == CWeatherTraits.HumidityOFL():
            self._HumidityIndoorMinMax._Max._IsOverflow = 1
        else:
            self._HumidityIndoorMinMax._Max._IsOverflow = 0
        if self._HumidityIndoorMinMax._Max._IsError or self._HumidityIndoorMinMax._Max._IsOverflow:
            self._HumidityIndoorMinMax._Max._Time = None
        else:
            self._HumidityIndoorMinMax._Max._Time = USBHardware.toDateTime(nbuf, 81, 1)
        if self._HumidityIndoorMinMax._Min._IsError or self._HumidityIndoorMinMax._Min._IsOverflow:
            self._HumidityIndoorMinMax._Min._Time = None
        else:
            self._HumidityIndoorMinMax._Min._Time = USBHardware.toDateTime(nbuf, 86, 1)

        self._HumidityOutdoorMinMax._Max._Value = USBHardware.toHumidity_2_0(nbuf, 104, 1)
        self._HumidityOutdoorMinMax._Min._Value = USBHardware.toHumidity_2_0(nbuf, 105, 1)
        self._HumidityOutdoor = USBHardware.toHumidity_2_0(nbuf, 106, 1)
        if self._HumidityOutdoorMinMax._Min._Value == CWeatherTraits.HumidityNP():
            self._HumidityOutdoorMinMax._Min._IsError = 1
        else:
            self._HumidityOutdoorMinMax._Min._IsError = 0
        if self._HumidityOutdoorMinMax._Min._Value == CWeatherTraits.HumidityOFL():
            self._HumidityOutdoorMinMax._Min._IsOverflow = 1
        else:
            self._HumidityOutdoorMinMax._Min._IsOverflow = 0

        if self._HumidityOutdoorMinMax._Max._Value == CWeatherTraits.HumidityNP():
            self._HumidityOutdoorMinMax._Max._IsError = 1
        else:
            self._HumidityOutdoorMinMax._Max._IsError = 0
        if self._HumidityOutdoorMinMax._Max._Value == CWeatherTraits.HumidityOFL():
            self._HumidityOutdoorMinMax._Max._IsOverflow = 1
        else:
            self._HumidityOutdoorMinMax._Max._IsOverflow = 0
        if self._HumidityOutdoorMinMax._Max._IsError or self._HumidityOutdoorMinMax._Max._IsOverflow:
            self._HumidityOutdoorMinMax._Max._Time = None
        else:
            self._HumidityOutdoorMinMax._Max._Time = USBHardware.toDateTime(nbuf, 94, 1)            
        if self._HumidityOutdoorMinMax._Min._IsError or self._HumidityOutdoorMinMax._Min._IsOverflow:
            self._HumidityOutdoorMinMax._Min._Time = None
        else:
            self._HumidityOutdoorMinMax._Min._Time = USBHardware.toDateTime(nbuf, 99, 1)

        self._RainLastMonthMax._Max._Time = USBHardware.toDateTime(nbuf, 107, 1)
        self._RainLastMonthMax._Max._Value = USBHardware.toRain_6_2(nbuf, 112, 1)
        self._RainLastMonth = USBHardware.toRain_6_2(nbuf, 115, 1)

        self._RainLastWeekMax._Max._Time = USBHardware.toDateTime(nbuf, 118, 1)
        self._RainLastWeekMax._Max._Value = USBHardware.toRain_6_2(nbuf, 123, 1)
        self._RainLastWeek = USBHardware.toRain_6_2(nbuf, 126, 1)

        self._Rain24HMax._Max._Time = USBHardware.toDateTime(nbuf, 129, 1)
        self._Rain24HMax._Max._Value = USBHardware.toRain_6_2(nbuf, 134, 1)
        self._Rain24H = USBHardware.toRain_6_2(nbuf, 137, 1)
        
        self._Rain1HMax._Max._Time = USBHardware.toDateTime(nbuf, 140, 1)
        self._Rain1HMax._Max._Value = USBHardware.toRain_6_2(nbuf, 145, 1)
        self._Rain1H = USBHardware.toRain_6_2(nbuf, 148, 1)

        self._LastRainReset = USBHardware.toDateTime(nbuf, 151, 0)
        self._RainTotal = USBHardware.toRain_7_3(nbuf, 156, 0)

        (w ,w1) = USBHardware.readWindDirectionShared(nbuf, 162)
        (w2,w3) = USBHardware.readWindDirectionShared(nbuf, 161)
        (w4,w5) = USBHardware.readWindDirectionShared(nbuf, 160)
        self._WindDirection = w;
        self._WindDirection1 = w1;
        self._WindDirection2 = w2;
        self._WindDirection3 = w3;
        self._WindDirection4 = w4;
        self._WindDirection5 = w5;

        unknownbuf = [0]
        unknownbuf[0] = [0]*9
        for i in xrange(0,9):
            unknownbuf[0][i] = nbuf[0][163+i]
        strbuf = ""
        for i in unknownbuf[0]:
            strbuf += str("%.2x " % i)
            
        self._WindSpeed = USBHardware.toWindspeed_5_2(nbuf, 172, 1)
        
        (g ,g1) = USBHardware.readWindDirectionShared(nbuf, 177)
        (g2,g3) = USBHardware.readWindDirectionShared(nbuf, 176)
        (g4,g5) = USBHardware.readWindDirectionShared(nbuf, 175)
        self._GustDirection = g;
        self._GustDirection1 = g1;
        self._GustDirection2 = g2;
        self._GustDirection3 = g3;
        self._GustDirection4 = g4;
        self._GustDirection5 = g5;

        self._GustMax._Max._Time = USBHardware.toDateTime(nbuf, 179, 1)
        self._GustMax._Max._Value = USBHardware.toWindspeed_5_2(nbuf, 184, 1)
        self._Gust = USBHardware.toWindspeed_5_2(nbuf, 187, 1)

        #lh The data has only ONE date time for both hPa/inHg Min Time Reset and Max Time Reset
        self._PressureRelative_hPaMinMax._Max._Time = USBHardware.toDateTime(nbuf, 190, 1)
        self._PressureRelative_inHgMinMax._Max._Time = self._PressureRelative_hPaMinMax._Max._Time
        self._PressureRelative_hPaMinMax._Min._Time  = self._PressureRelative_hPaMinMax._Max._Time # WS bug, should be: USBHardware.toDateTime(nbuf, 195, 1)
        self._PressureRelative_inHgMinMax._Min._Time = self._PressureRelative_hPaMinMax._Min._Time        

        (self._PresRel_hPa_Max, self._PresRel_inHg_Max) = USBHardware.readPressureShared(nbuf, 195, 1) #bug in WS; here should go self._PressureRelative_hPaMinMax._Min._Time
        (self._PressureRelative_hPaMinMax._Max._Value, self._PressureRelative_inHgMinMax._Max._Value) = USBHardware.readPressureShared(nbuf, 200, 1)
        (self._PressureRelative_hPaMinMax._Min._Value, self._PressureRelative_inHgMinMax._Min._Value) = USBHardware.readPressureShared(nbuf, 205, 1)
        (self._PressureRelative_hPa, self._PressureRelative_inHg) = USBHardware.readPressureShared(nbuf, 210, 1)

        self._timestamp = time.time()
        logdbg("_WeatherState=%s _WeatherTendency=%s _AlarmRingingFlags %04x" % (CWeatherTraits.forecastMap[self._WeatherState], CWeatherTraits.trends[self._WeatherTendency], self._AlarmRingingFlags))
        logdbg("_TempIndoor=     %8.3f _Min=%8.3f (%s)  _Max=%8.3f (%s)" % (self._TempIndoor, self._TempIndoorMinMax._Min._Value, self._TempIndoorMinMax._Min._Time, self._TempIndoorMinMax._Max._Value, self._TempIndoorMinMax._Max._Time))
        logdbg("_HumidityIndoor= %8.3f _Min=%8.3f (%s)  _Max=%8.3f (%s)" % (self._HumidityIndoor, self._HumidityIndoorMinMax._Min._Value, self._HumidityIndoorMinMax._Min._Time, self._HumidityIndoorMinMax._Max._Value, self._HumidityIndoorMinMax._Max._Time))
        logdbg("_TempOutdoor=    %8.3f _Min=%8.3f (%s)  _Max=%8.3f (%s)" % (self._TempOutdoor, self._TempOutdoorMinMax._Min._Value, self._TempOutdoorMinMax._Min._Time, self._TempOutdoorMinMax._Max._Value, self._TempOutdoorMinMax._Max._Time))
        logdbg("_HumidityOutdoor=%8.3f _Min=%8.3f (%s)  _Max=%8.3f (%s)" % (self._HumidityOutdoor, self._HumidityOutdoorMinMax._Min._Value, self._HumidityOutdoorMinMax._Min._Time, self._HumidityOutdoorMinMax._Max._Value, self._HumidityOutdoorMinMax._Max._Time))
        logdbg("_Windchill=      %8.3f _Min=%8.3f (%s)  _Max=%8.3f (%s)" % (self._Windchill, self._WindchillMinMax._Min._Value, self._WindchillMinMax._Min._Time, self._WindchillMinMax._Max._Value, self._WindchillMinMax._Max._Time))
        logdbg("_Dewpoint=       %8.3f _Min=%8.3f (%s)  _Max=%8.3f (%s)" % (self._Dewpoint, self._DewpointMinMax._Min._Value, self._DewpointMinMax._Min._Time, self._DewpointMinMax._Max._Value, self._DewpointMinMax._Max._Time))
        logdbg("_WindSpeed=      %8.3f" % self._WindSpeed)
        logdbg("_Gust=           %8.3f                                      _Max=%8.3f (%s)" % (self._Gust, self._GustMax._Max._Value, self._GustMax._Max._Time))
        logdbg('_WindDirection=    %3s    _GustDirection=    %3s' % (CWeatherTraits.windDirMap[self._WindDirection],  CWeatherTraits.windDirMap[self._GustDirection]))
        logdbg('_WindDirection1=   %3s    _GustDirection1=   %3s' % (CWeatherTraits.windDirMap[self._WindDirection1], CWeatherTraits.windDirMap[self._GustDirection1]))
        logdbg('_WindDirection2=   %3s    _GustDirection2=   %3s' % (CWeatherTraits.windDirMap[self._WindDirection2], CWeatherTraits.windDirMap[self._GustDirection2]))
        logdbg('_WindDirection3=   %3s    _GustDirection3=   %3s' % (CWeatherTraits.windDirMap[self._WindDirection3], CWeatherTraits.windDirMap[self._GustDirection3]))
        logdbg('_WindDirection4=   %3s    _GustDirection4=   %3s' % (CWeatherTraits.windDirMap[self._WindDirection4], CWeatherTraits.windDirMap[self._GustDirection4]))
        logdbg('_WindDirection5=   %3s    _GustDirection5=   %3s' % (CWeatherTraits.windDirMap[self._WindDirection5], CWeatherTraits.windDirMap[self._GustDirection5]))
        if (self._RainLastMonth > 0) or (self._RainLastWeek > 0):
            logdbg("_RainLastMonth=  %8.3f                                      _Max=%8.3f (%s)" % (self._RainLastMonth, self._RainLastMonthMax._Max._Value, self._RainLastMonthMax._Max._Time))
            logdbg("_RainLastWeek=   %8.3f                                      _Max=%8.3f (%s)" % (self._RainLastWeek, self._RainLastWeekMax._Max._Value, self._RainLastWeekMax._Max._Time))
        logdbg("_Rain24H=        %8.3f                                      _Max=%8.3f (%s)" % (self._Rain24H, self._Rain24HMax._Max._Value, self._Rain24HMax._Max._Time))
        logdbg("_Rain1H=         %8.3f                                      _Max=%8.3f (%s)" % (self._Rain1H, self._Rain1HMax._Max._Value, self._Rain1HMax._Max._Time))
        logdbg("_RainTotal=      %8.3f                            _LastRainReset=         (%s)" % (self._RainTotal,  self._LastRainReset))
        logdbg("PressureRel_hPa= %8.3f _Min=%8.3f (%s)  _Max=%8.3f (%s) " % (self._PressureRelative_hPa, self._PressureRelative_hPaMinMax._Min._Value, self._PressureRelative_hPaMinMax._Min._Time, self._PressureRelative_hPaMinMax._Max._Value, self._PressureRelative_hPaMinMax._Max._Time))                       
        logdbg("PressureRel_inHg=%8.3f _Min=%8.3f (%s)  _Max=%8.3f (%s) " % (self._PressureRelative_inHg, self._PressureRelative_inHgMinMax._Min._Value, self._PressureRelative_inHgMinMax._Min._Time, self._PressureRelative_inHgMinMax._Max._Value, self._PressureRelative_inHgMinMax._Max._Time))                       
        ###logdbg('(* Bug in Weather Station: PressureRelative._Min._Time is written to location of _PressureRelative._Max._Time')
        ###logdbg('Instead of PressureRelative._Min._Time we get: _PresRel_hPa_Max= %8.3f, _PresRel_inHg_max =%8.3f;' % (self._PresRel_hPa_Max, self._PresRel_inHg_Max))
        logdbg('Bytes with unknown meaning at 157-165: %s' % strbuf) 
        
class CWeatherStationConfig(object):
    def __init__(self,cfgfn):
        logdbg('CWeatherStationConfig_init %s' % cfgfn)
        self._InBufCS = 0  # checksum of received config
        self._OutBufCS = 0 # Calculated config checksum from outbuf config
        config = ConfigObj(cfgfn)
        config.filename = cfgfn
        try:
            self._DeviceCS = int(config['ws28xx']['DeviceCS']) # Actual config checksum received via messages
        except:
            self._DeviceCS = 0
        self.filename= CFGFILE
        self._ClockMode = 0
        self._TemperatureFormat = 0
        self._PressureFormat = 0
        self._RainFormat = 0
        self._WindspeedFormat = 0
        self._WeatherThreshold = 0
        self._StormThreshold = 0
        self._LCDContrast = 0
        self._LowBatFlags = 0
        """
        lh WARNING
        Don't set WindDirAlarmFlags and OtherAlarmFlags with this program (other than 0x0000), 
        because during an alarm -until reset by the user- no data will be sent by the 
        weather station and eventually synchronisation will be lost.
        """
        self._WindDirAlarmFlags = 0
        self._OtherAlarmFlags = 0
        
        self._ResetMinMaxFlags = 0 #lh Output only
        self._HistoryInterval = 0
        self._TempIndoorMinMax = CMinMaxMeasurement()
        self._TempOutdoorMinMax = CMinMaxMeasurement()
        self._HumidityIndoorMinMax = CMinMaxMeasurement()
        self._HumidityOutdoorMinMax = CMinMaxMeasurement()
        self._Rain24HMax = CMinMaxMeasurement()
        self._GustMax = CMinMaxMeasurement()
        self._PressureRelative_hPaMinMax = CMinMaxMeasurement()
        self._PressureRelative_inHgMinMax = CMinMaxMeasurement()

    def readAlertFlags(self,buf):
        logdbg('readAlertFlags')

    def setTemps(self,TempFormat,InTempLo,InTempHi,OutTempLo,OutTempHi):
        logdbg('setTemps')
        f1 = TempFormat
        t1 = InTempLo
        t2 = InTempHi
        t3 = OutTempLo
        t4 = OutTempHi
        if (f1 == ETemperatureFormat.tfFahrenheit) or (f1 == ETemperatureFormat.tfCelsius):
            if ((t1 >= -40.0) and (t1 <= 59.9) and (t2 >= -40.0) and (t2 <= 59.9) and \
                (t3 >= -40.0) and (t3 <= 59.9) and (t4 >= -40.0) and (t4 <= 59.9)):
                self._TemperatureFormat = f1
            else:
                logerr('Value outside range')
                return 0
        else:
            logerr('Unknown format')
            return 0
        self._TempIndoorMinMax._Min._Value = t1
        self._TempIndoorMinMax._Max._Value = t2
        self._TempOutdoorMinMax._Min._Value = t3
        self._TempOutdoorMinMax._Max._Value = t4
        return 1     
    
    def setHums(self,InHumLo,InHumHi,OutHumLo,OutHumHi):
        h1 = InHumLo
        h2 = InHumHi
        h3 = OutHumLo
        h4 = OutHumHi 
        if not ((h1 >= 1) and (h1 <= 99) and (h2 >= 1) and (h2 <= 99) and \
            (h3 >= 1) and (h3 <= 99) and (h4 >= 1) and (h4 <= 99)):
            logerr('Humidity value outside range')
            return 0
        self._HumidityIndoorMinMax._Min._Value = h1
        self._HumidityIndoorMinMax._Max._Value = h2
        self._HumidityOutdoorMinMax._Min._Value = h3
        self._HumidityOutdoorMinMax._Max._Value = h4
        return 1
    
    def setRain24H(self,RainFormat,Rain24hHi):
        f1 = RainFormat
        r1 = Rain24hHi 
        if (f1 == ERainFormat.rfMm) or (f1 == ERainFormat.rfInch):
            if (r1>=0.0) and (r1 <= 9999.9):
                self._RainFormat = f1
            else:
                logerr('Rain24H value outside range')
                return 0
        else:
            logerr('Unknown RainFormat')
            return 0
        self._Rain24HMax._Max._Value = r1
        return 1
    
    def setGust(self,WindSpeedFormat,GustHi):
        f1 = WindSpeedFormat
        g1 = GustHi
        if (f1 >= EWindspeedFormat.wfMs) and (f1 <= EWindspeedFormat.wfMph):
            if (g1>=0.0) and (g1 <= 180.0):
                self._WindSpeedFormat = f1
            else:
                logerr('Gust value outside range')
                return 0 
        else:
            logerr('Unknown WindSpeedFormat')
            return 0
        self._GustMax._Max._Value = g1
        return 1
    
    def setPresRels(self,PressureFormat,PresRelhPaLo,PresRelhPaHi,PresRelinHgLo,PresRelinHgHi):
        f1 = PressureFormat
        p1 = PresRelhPaLo
        p2 = PresRelhPaHi
        p3 = PresRelinHgLo
        p4 = PresRelinHgHi
        if (f1 == EPressureFormat.pfinHg) or (f1 == EPressureFormat.pfHPa):
            if ((p1>=920.0) and (p1 <= 1080.0) and (p2>=920.0) and (p2 <= 1080.0) and \
                (p3>=27.10) and (p3 <= 31.90) and (p4>=27.10) and (p4 <= 31.90)):
                self._RainFormat = f1
            else:
                logerr('PresRel value outside range')
                return 0
        else:
            logerr('Unknown PressureFormat')
            return 0
        self._PressureRelative_hPaMinMax._Min._Value = p1
        self._PressureRelative_hPaMinMax._Max._Value = p2
        self._PressureRelative_inHgMinMax._Min._Value = p3
        self._PressureRelative_inHgMinMax._Max._Value = p4
        return 1

    def CWeatherStationConfig_buf(self,buf,start):
        nbuf=[0]
        nbuf[0] = buf[0]
        self.readConfig(nbuf,start);

    def calcOutBufCS(self, buf, start):
        # For the calculation of the CheckSum the _ResetMinMaxFlags
        # and the Checksum itself are excluded.
        nbuf=[0]
        nbuf[0]=buf[0]
        outbufCS = 7
        for i in xrange(0, 39):
            outbufCS += nbuf[0][i+start]
        logdbg('calcOutBufCS=%04x' % outbufCS)
        return outbufCS
    
    def getOutBufCS(self):
        logdbg('getOutBufCS')
        return self._OutBufCS
             
    def getInBufCS(self):
        logdbg('getInBufCS')
        return self._InBufCS
    
    def setDeviceCS(self, deviceCS):
        logdbg('setDeviceCS')
        self._DeviceCS = deviceCS
        
    def getDeviceCS(self):
        logdbg('getDeviceCS')
        return self._DeviceCS
    
    def setResetMinMaxFlags(self, resetMinMaxFlags):
        logdbg('setResetMinMaxFlags')
        self._ResetMinMaxFlags = resetMinMaxFlags

    def parseRain_3(self, number, buf, start, StartOnHiNibble, numbytes): #Parse 7-digit number with 3 decimals 
        num = int(number*1000)
        parsebuf=[0]*7
        for i in xrange(7-numbytes,7):
            parsebuf[i] = num%10
            num = num//10
        if StartOnHiNibble:
                buf[0][0+start] = parsebuf[6]*16 + parsebuf[5]
                buf[0][1+start] = parsebuf[4]*16 + parsebuf[3]
                buf[0][2+start] = parsebuf[2]*16 + parsebuf[1]
                buf[0][3+start] = parsebuf[0]*16 + (buf[0][3+start] & 0xF)
        else:
                buf[0][0+start] = (buf[0][0+start] & 0xF0) + parsebuf[6]
                buf[0][1+start] = parsebuf[5]*16 + parsebuf[4]
                buf[0][2+start] = parsebuf[3]*16 + parsebuf[2]
                buf[0][3+start] = parsebuf[1]*16 + parsebuf[0]
                        
    def parseWind_2(self, number, buf, start, StartOnHiNibble, numbytes): #Parse 4-digit number with 1 decimal 
        num = int(number*100)
        parsebuf=[0]*5
        for i in xrange(5-numbytes,5):
            parsebuf[i] = num%16
            num = num//16
        buf[0][0+start] = parsebuf[3]*16 + parsebuf[2]
        buf[0][1+start] = parsebuf[1]*16 + parsebuf[0]
        
    def parse_0(self, number, buf, start, StartOnHiNibble, numbytes): #Parse 5-digit number with 0 decimals 
        num = int(number)
        nbuf=[0]*5
        for i in xrange(5-numbytes,5):
            nbuf[i] = num%10
            num = num//10
        if StartOnHiNibble:
            buf[0][0+start] = nbuf[4]*16 + nbuf[3]
            buf[0][1+start] = nbuf[2]*16 + nbuf[1]
            buf[0][2+start] = nbuf[0]*16 + (buf[0][2+start] & 0x0F)
        else:
            buf[0][0+start] = (buf[0][0+start] & 0xF0) + nbuf[4]
            buf[0][1+start] = nbuf[3]*16 + nbuf[2]
            buf[0][2+start] = nbuf[1]*16 + nbuf[0]

    def parse_1(self, number, buf, start, StartOnHiNibble, numbytes): #Parse 5 digit number with 1 decimal
        self.parse_0(number*10.0, buf, start, StartOnHiNibble, numbytes)
    
    def parse_2(self, number, buf, start, StartOnHiNibble, numbytes): #Parse 5 digit number with 2 decimals
        self.parse_0(number*100.0, buf, start, StartOnHiNibble, numbytes)
    
    def parse_3(self, number, buf, start, StartOnHiNibble, numbytes): #Parse 5 digit number with 3 decimals
        self.parse_0(number*1000.0, buf, start, StartOnHiNibble, numbytes)
    
    def writeConfig(self):
        self.filename = CFGFILE
        config = ConfigObj(self.filename)
        config.filename = self.filename
        config['ws28xx'] = {}
        config['ws28xx']['DeviceCS'] = str(self._DeviceCS)
        config['ws28xx']['ClockMode'] = str(self._ClockMode)
        config['ws28xx']['TemperatureFormat'] = str(self._TemperatureFormat)
        config['ws28xx']['PressureFormat'] = str(self._PressureFormat)
        config['ws28xx']['RainFormat'] = str(self._RainFormat)
        config['ws28xx']['WindspeedFormat'] = str(self._WindspeedFormat)
        config['ws28xx']['WeatherThreshold'] = str(self._WeatherThreshold)
        config['ws28xx']['StormThreshold'] = str(self._StormThreshold)
        config['ws28xx']['LCDContrast'] = str(self._LCDContrast)
        config['ws28xx']['LowBatFlags'] = str(self._LowBatFlags)
        config['ws28xx']['WindDirAlarmFlags'] = str(self._WindDirAlarmFlags)
        config['ws28xx']['OtherAlarmFlags'] = str(self._OtherAlarmFlags)
        config['ws28xx']['HistoryInterval'] = str(self._HistoryInterval)
        config['ws28xx']['ResetMinMaxFlags'] = str(self._ResetMinMaxFlags)
        config['ws28xx']['TempIndoor_Min'] = str(self._TempIndoorMinMax._Min._Value)
        config['ws28xx']['TempIndoor_Max'] = str(self._TempIndoorMinMax._Max._Value)
        config['ws28xx']['Outdoor_Min'] = str(self._TempOutdoorMinMax._Min._Value)
        config['ws28xx']['TempOutdoorMax'] = str(self._TempOutdoorMinMax._Max._Value)
        config['ws28xx']['HumidityIndoor_Min'] = str(self._HumidityIndoorMinMax._Min._Value)
        config['ws28xx']['HumidityIndoor_Max'] = str(self._HumidityIndoorMinMax._Max._Value)
        config['ws28xx']['HumidityOutdoor_Min'] = str(self._HumidityOutdoorMinMax._Min._Value)
        config['ws28xx']['HumidityOutdoor_Max'] = str(self._HumidityOutdoorMinMax._Max._Value)
        config['ws28xx']['Rain24HMax'] = str(self._Rain24HMax._Max._Value)
        config['ws28xx']['GustMax'] = str(self._GustMax._Max._Value)
        config['ws28xx']['PressureRel_hPa_Min'] = str(self._PressureRelative_hPaMinMax._Min._Value)
        config['ws28xx']['PressureRel_inHg_Min'] = str(self._PressureRelative_inHgMinMax._Min._Value)
        config['ws28xx']['PressureRel_hPa_Max'] = str(self._PressureRelative_hPaMinMax._Max._Value)
        config['ws28xx']['PressureRel_inHg_Max'] = str(self._PressureRelative_inHgMinMax._Max._Value)
        config.write()
        
    def readConfig(self,buf,pos):
        logdbg('readConfig')
        nbuf=[0]
        nbuf[0]=buf[0]
        ###USBHardware.dumpBuf('In  ', nbuf[0], 0x30)
        self._WindspeedFormat = (nbuf[0][4] >> 4) & 0xF;  
        self._RainFormat = (nbuf[0][4] >> 3) & 1;
        self._PressureFormat = (nbuf[0][4] >> 2) & 1;
        self._TemperatureFormat = (nbuf[0][4] >> 1) & 1;
        self._ClockMode = nbuf[0][4] & 1;
        self._StormThreshold = (nbuf[0][5] >> 4) & 0xF;
        self._WeatherThreshold = nbuf[0][5] & 0xF;
        self._LowBatFlags = (nbuf[0][6] >> 4) & 0xF;
        self._LCDContrast = nbuf[0][6] & 0xF;
        self._WindDirAlarmFlags = (nbuf[0][7] << 8) | nbuf[0][8]
        self._OtherAlarmFlags = (nbuf[0][9] << 8) | nbuf[0][10]
        self._TempIndoorMinMax._Max._Value = USBHardware.toTemperature_5_3(nbuf, 11, 1);
        self._TempIndoorMinMax._Min._Value = USBHardware.toTemperature_5_3(nbuf, 13, 0);
        self._TempOutdoorMinMax._Max._Value = USBHardware.toTemperature_5_3(nbuf, 16, 1);
        self._TempOutdoorMinMax._Min._Value = USBHardware.toTemperature_5_3(nbuf, 18, 0);
        self._HumidityIndoorMinMax._Max._Value = USBHardware.toHumidity_2_0(nbuf, 21, 1)
        self._HumidityIndoorMinMax._Min._Value = USBHardware.toHumidity_2_0(nbuf, 22, 1)
        self._HumidityOutdoorMinMax._Max._Value = USBHardware.toHumidity_2_0(nbuf, 23, 1)
        self._HumidityOutdoorMinMax._Min._Value = USBHardware.toHumidity_2_0(nbuf, 24, 1)
        self._Rain24HMax._Max._Value = USBHardware.toRain_7_3(nbuf, 25, 0);
        self._HistoryInterval = nbuf[0][29]
        self._GustMax._Max._Value = USBHardware.toWindspeed_5_2(nbuf, 30, 1)
        (self._PressureRelative_hPaMinMax._Min._Value, self._PressureRelative_inHgMinMax._Min._Value) = USBHardware.readPressureShared(nbuf, 33, 1)
        (self._PressureRelative_hPaMinMax._Max._Value, self._PressureRelative_inHgMinMax._Max._Value) = USBHardware.readPressureShared(nbuf, 38, 1)
        self._ResetMinMaxFlags = (nbuf[0][43]) <<16 | (nbuf[0][44] << 8) | (nbuf[0][45])
        self._InBufCS = (nbuf[0][46] << 8) | nbuf[0][47]
        
        self._OutBufCS = self.calcOutBufCS(buf,pos)        
        self.logConfigData()
        self.writeConfig()

        ###self._ResetMinMaxFlags = 0x000000
        ###logdbg('set _ResetMinMaxFlags to %06x' % self._ResetMinMaxFlags)
        """
        #Reset DewpointMax    80 00 00
        #Reset DewpointMin    40 00 00 
        #not used             20 00 00 
        #Reset WindchillMin*  10 00 00  *Reset dateTime only; Min._Value is preserved
                
        #Reset TempOutMax     08 00 00
        #Reset TempOutMin     04 00 00
        #Reset TempInMax      02 00 00
        #Reset TempInMin      01 00 00 
         
        #Reset Gust           00 80 00
        #not used             00 40 00
        #not used             00 20 00
        #not used             00 10 00 
         
        #Reset HumOutMax      00 08 00
        #Reset HumOutMin      00 04 00 
        #Reset HumInMax       00 02 00 
        #Reset HumInMin       00 01 00 
          
        #not used             00 00 80
        #Reset Rain Total     00 00 40
        #Reset last month?    00 00 20
        #Reset last week?     00 00 10 
         
        #Reset Rain24H        00 00 08
        #Reset Rain1H         00 00 04 
        #Reset PresRelMax     00 00 02 
        #Reset PresRelMin     00 00 01                 
        """

        ###logdbg('Preset Config data')
        """
        setTemps(self,TempFormat,InTempLo,InTempHi,OutTempLo,OutTempHi) 
        setHums(self,InHumLo,InHumHi,OutHumLo,OutHumHi)
        setPresRels(self,PressureFormat,PresRelhPaLo,PresRelhPaHi,PresRelinHgLo,PresRelinHgHi)  
        setGust(self,WindSpeedFormat,GustHi)
        setRain24H(self,RainFormat,Rain24hHi)
        """
        # Examples:
        ###self.setTemps(ETemperatureFormat.tfCelsius,1.0,41.0,2.0,42.0) 
        ###self.setHums(41,71,42,72)
        ###self.setPresRels(EPressureFormat.pfHPa,960.1,1040.1,28.36,30.72)
        ###self.setGust(EWindspeedFormat.wfKmh,100.0)
        ##self.setRain24H(ERainFormat.rfMm,50.0)        

        # Preset historyInterval to 1 minute (default: 2 hours)
        self._HistoryInterval = EHistoryInterval.hi24Std #hi01Min
        # Clear all alarm flags, because the datastream from the weather station will pauze during an alarm
        ###self._WindDirAlarmFlags = 0x0000
        ###self._OtherAlarmFlags   = 0x0000
        return 1
    
    def testConfigChanged(self,buf):
        logdbg('CweatherStationConfig_testConfigChanged')
        nbuf = [0]
        nbuf[0] = buf[0]
        nbuf[0][0] = 16*(self._WindspeedFormat & 0xF) + 8*(self._RainFormat & 1) + 4*(self._PressureFormat & 1) + 2*(self._TemperatureFormat & 1) + (self._ClockMode & 1)
        nbuf[0][1] = self._WeatherThreshold & 0xF | 16 * self._StormThreshold & 0xF0;
        nbuf[0][2] = self._LCDContrast & 0xF | 16 * self._LowBatFlags & 0xF0;
        nbuf[0][3] = (self._OtherAlarmFlags >> 0) & 0xFF
        nbuf[0][4] = (self._OtherAlarmFlags >> 8) & 0xFF
        nbuf[0][5] = (self._WindDirAlarmFlags >> 0) & 0xFF
        nbuf[0][6] = (self._WindDirAlarmFlags >> 8) & 0xFF
        # reverse buf from here
        self.parse_2(self._PressureRelative_inHgMinMax._Max._Value, nbuf, 7, 1, 5)
        self.parse_1(self._PressureRelative_hPaMinMax._Max._Value, nbuf, 9, 0, 5)
        self.parse_2(self._PressureRelative_inHgMinMax._Min._Value, nbuf, 12, 1, 5)
        self.parse_1(self._PressureRelative_hPaMinMax._Min._Value, nbuf, 14, 0, 5)
        self.parseWind_2(self._GustMax._Max._Value, nbuf, 17, 0, 5)
        nbuf[0][20] = self._HistoryInterval & 0xF;
        self.parseRain_3(self._Rain24HMax._Max._Value, nbuf, 21, 0, 7)
        self.parse_0(self._HumidityOutdoorMinMax._Max._Value, nbuf, 25, 1, 2)
        self.parse_0(self._HumidityOutdoorMinMax._Min._Value, nbuf, 26, 1, 2)
        self.parse_0(self._HumidityIndoorMinMax._Max._Value, nbuf, 27, 1, 2)
        self.parse_0(self._HumidityIndoorMinMax._Min._Value, nbuf, 28, 1, 2)
        self.parse_3(self._TempOutdoorMinMax._Max._Value + CWeatherTraits.TemperatureOffset(), nbuf, 29, 1, 5)
        self.parse_3(self._TempOutdoorMinMax._Min._Value + CWeatherTraits.TemperatureOffset(), nbuf, 31, 0, 5)
        self.parse_3(self._TempIndoorMinMax._Max._Value + CWeatherTraits.TemperatureOffset(), nbuf, 34, 1, 5)
        self.parse_3(self._TempIndoorMinMax._Min._Value + CWeatherTraits.TemperatureOffset(), nbuf, 36, 0, 5)
        # reverse buf to here
        USBHardware.reverseByteOrder(nbuf, 7, 32);
        nbuf[0][39] = (self._ResetMinMaxFlags >> 16) & 0xFF;  #lh Don't calculate CheckSum 
        nbuf[0][40] = (self._ResetMinMaxFlags >>  8) & 0xFF;  #   for the 3 (output only)
        nbuf[0][41] = (self._ResetMinMaxFlags >>  0) & 0xFF;  #   _ResetMinMaxFlags bytes
        self._OutBufCS = self.calcOutBufCS(nbuf,0)
        nbuf[0][42] = (self._OutBufCS >> 8) & 0xFF
        nbuf[0][43] = (self._OutBufCS >> 0) & 0xFF
        buf[0] = nbuf[0]   
        if (self._OutBufCS == self._InBufCS) and (self._ResetMinMaxFlags  == 0):
            logdbg('testConfigChanged: checksum not changed %04x' % self._OutBufCS)
            State = 0
        else:
            loginf('Checksum or resetMinMaxFlags changed, InBufCS=%04x, OutBufCS=%04x, _ResetMinMaxFlags=%06x' % (self._InBufCS, self._OutBufCS, self._ResetMinMaxFlags))
            self.logConfigData()
            self.writeConfig()
            State = 1
        return State

    def logConfigData(self):
        loginf('OutBufCS=             %04x' % self._OutBufCS)
        loginf('InBufCS=              %04x' % self._InBufCS)
        loginf('DeviceCS=             %04x' % self._DeviceCS)
        logdbg('ClockMode=            %s' % self._ClockMode)
        logdbg('TemperatureFormat=    %s' % self._TemperatureFormat)
        logdbg('PressureFormat=       %s' % self._PressureFormat)
        logdbg('RainFormat=           %s' % self._RainFormat)
        logdbg('WindspeedFormat=      %s' % self._WindspeedFormat)
        logdbg('WeatherThreshold=     %s' % self._WeatherThreshold)
        logdbg('StormThreshold=       %s' % self._StormThreshold)
        logdbg('LCDContrast=          %s' % self._LCDContrast)
        if self._LowBatFlags > 0:
            loginf('LET OP: LowBatFlags=  %01x' % self._LowBatFlags)
        else:
            logdbg('LowBatFlags=          %01x' % self._LowBatFlags)
        logdbg('WindDirAlarmFlags=    %04x' % self._WindDirAlarmFlags)
        logdbg('OtherAlarmFlags=      %04x' % self._OtherAlarmFlags)
        loginf('HistoryInterval=      %s' % self._HistoryInterval)
        logdbg('TempIndoor_Min=       %s' % self._TempIndoorMinMax._Min._Value)
        logdbg('TempIndoor_Max=       %s' % self._TempIndoorMinMax._Max._Value)
        logdbg('TempOutdoor_Min=      %s' % self._TempOutdoorMinMax._Min._Value)
        logdbg('TempOutdoor_Max=      %s' % self._TempOutdoorMinMax._Max._Value)
        logdbg('HumidityIndoor_Min=   %s' % self._HumidityIndoorMinMax._Min._Value)
        logdbg('HumidityIndoor_Max=   %s' % self._HumidityIndoorMinMax._Max._Value)
        logdbg('HumidityOutdoor_Min=  %s' % self._HumidityOutdoorMinMax._Min._Value)
        logdbg('HumidityOutdoor_Max=  %s' % self._HumidityOutdoorMinMax._Max._Value)
        logdbg('Rain24HMax=           %s' % self._Rain24HMax._Max._Value)
        logdbg('GustMax=              %s' % self._GustMax._Max._Value)
        logdbg('PressureRel_hPa_Min=  %s' % self._PressureRelative_hPaMinMax._Min._Value)
        logdbg('PressureRel_inHg_Min= %s' % self._PressureRelative_inHgMinMax._Min._Value)
        logdbg('PressureRel_hPa_Max=  %s' % self._PressureRelative_hPaMinMax._Max._Value)
        logdbg('PressureRel_inHg_Max= %s' % self._PressureRelative_inHgMinMax._Max._Value) 
        logdbg('ResetMinMaxFlags=     %06x (Output only)' % self._ResetMinMaxFlags) 

class CHistoryDataSet(object):

    def __init__(self):
        logdbg('CHistoryDataSet_init')
        self.m_Time = None
        self.m_TempIndoor = CWeatherTraits.TemperatureNP()
        self.m_HumidityIndoor = CWeatherTraits.HumidityNP()
        self.m_TempOutdoor = CWeatherTraits.TemperatureNP()
        self.m_HumidityOutdoor = CWeatherTraits.HumidityNP()
        self.m_PressureRelative = None
        self.m_WindDirection = 16
        self.m_RainCounterRaw = 0
        self.m_WindSpeed = CWeatherTraits.WindNP()
        self.m_Gust = CWeatherTraits.WindNP()

    def CHistoryDataSet_buf(self,buf,pos):
        logdbg('CHistoryDataSet_buf')

        self.readHistory(buf,pos)

    def readHistory(self,buf,pos):
        logdbg('readHistory')
        nbuf = [0]
        nbuf[0] = buf[0]
        ###USBHardware.dumpBuf('His ', nbuf[0], 0x1e) 
        self.m_Gust = USBHardware.toWindspeed_3_1(nbuf, 12, 0)
        self.m_WindDirection = (nbuf[0][14] >> 4) & 0xF
        self.m_WindSpeed = USBHardware.toWindspeed_3_1(nbuf, 14, 0)
        if ( self.m_WindSpeed == CWeatherTraits.WindNP() ):
            self.m_WindDirection = 16
        if ( self.m_WindDirection < 0 and self.m_WindDirection > 16 ):
            self.m_WindDirection = 16 
        self.m_RainCounterRaw = USBHardware.toRain_3_1(nbuf, 16, 1)
        self.m_HumidityOutdoor = USBHardware.toHumidity_2_0(nbuf, 17, 0)
        self.m_HumidityIndoor = USBHardware.toHumidity_2_0(nbuf, 18, 0)    
        #self.m_PressureAbsolute = CWeatherTraits.PressureNP(); #I think this should be sum to np.
        self.m_PressureRelative = USBHardware.toPressure_hPa_5_1(nbuf, 19, 0)
        self.m_TempIndoor = USBHardware.toTemperature_3_1(nbuf, 23, 0)
        self.m_TempOutdoor = USBHardware.toTemperature_3_1(nbuf, 22, 1)                   
        self.m_Time = USBHardware.toDateTime(nbuf, 25, 1)
        logdbg("m_Time           %s"    % self.m_Time)
        logdbg("m_TempIndoor=       %7.1f" % self.m_TempIndoor)
        logdbg("m_HumidityIndoor=   %7.0f" % self.m_HumidityIndoor)
        logdbg("m_TempOutdoor=      %7.1f" % self.m_TempOutdoor)
        logdbg("m_HumidityOutdoor=  %7.0f" % self.m_HumidityOutdoor)
        logdbg("m_PressureRelative= %7.1f" % self.m_PressureRelative)
        logdbg("m_RainCounterRaw=   %7.1f" % self.m_RainCounterRaw)
        logdbg("m_WindDirection=        %.3s" % CWeatherTraits.windDirMap[self.m_WindDirection])
        logdbg("m_WindSpeed=        %7.1f" % self.m_WindSpeed)
        logdbg("m_Gust=             %7.1f" % self.m_Gust)

class CDataStore(object):

    class TTransceiverSettings(object): 
        def __init__(self):
            logdbg('TTransceiverSettings_init') 
            self.VendorId    = 0x6666
            self.ProductId    = 0x5555
            self.VersionNo    = 1
            self.Frequency    = ETransmissionFrequency.tfUSFreq
            self.TransmissionFrequency = ETransmissionFrequency.tfUS
            self.manufacturer    = "LA CROSSE TECHNOLOGY"
            self.product        = "Weather Direct Light Wireless Device"

    class TRequest(object):
        def __init__(self):
            logdbg('TRequest_init') 
            self.Type = ERequestType.rtINVALID
            self.State = ERequestState.rsError
            self.TTL = 90000
            self.Lock = threading.Lock()
            self.CondFinish = threading.Condition()

    class TLastStat(object):
        def __init__(self):
            logdbg('TLastStat_init') 
            self.LastBatteryStatus = [0]
            self.LastLinkQuality = 0
            self.OutstandingHistorySets = -1
            self.WeatherClubTransmissionErrors = 0
            self.LastCurrentWeatherTime = datetime(1900, 01, 01, 00, 00)
            self.LastHistoryDataTime = datetime(1900, 01, 01, 00, 00)
            self.LastConfigTime = datetime(1900, 01, 01, 00, 00)
            self.LastWeatherClubTransmission = None
            self.LastSeen = None

            self.filename = TMPCFG
            config = ConfigObj(self.filename)
            config.filename = self.filename
            try:
                self.LastHistoryIndex = int(config['LastStat']['LastHistoryIndex'])
            except:
                self.LastHistoryIndex = 0xFFFF
                pass

    class TSettings(object):
        def __init__(self):
            logdbg('TSettings_init')
            #self.CommModeInterval = 0 #lh was: 3
            self.DeviceId = -1
            self.DeviceRegistered = False
            self.PreambleDuration = 5000
            self.RegisterWaitTime = 20000
            self.TransceiverIdChanged = None
            self.TransceiverID = -1
            
    def __init__(self, cfgfn):
        logdbg('CDataStore_init %s' % cfgfn)
        #self.MemSegment = shelve???? o mmap??
        #self.DataStoreAllocator = shelve???? mmap???
        self.filename = cfgfn
        self.Guards = 0;
        self.Flags = 0;
        self.WeatherClubSettings = 0;
        self.HistoryData = CHistoryDataSet();
        self.CurrentWeather = CCurrentWeatherData();
        self.WeatherStationConfig = CWeatherStationConfig(CFGFILE)
        self.FrontEndConfig = 0;
        self.Request = 0;
        self.LastHistTimeStamp = 0;
        self.BufferCheck = 0;
        self.Request = CDataStore.TRequest()
#        self.Request.CondFinish = threading.Condition()
        self.LastStat = CDataStore.TLastStat()
        self.Settings = CDataStore.TSettings()
        self.TransceiverSettings = CDataStore.TTransceiverSettings()
        self.TransceiverSerNo = None
        self.TransceiveID = None

    #ShelveDataStore=shelve.open("WV5DataStore",writeback=True)
    #if ShelveDataStore.has_key("Settings"):
    #    self.DataStore.Settings = ShelveDataStore["Settings"]
    #else:
    #    print ShelveDataStore.keys()

    def writeLastStat(self):
        self.filename = TMPCFG
        config = ConfigObj(self.filename)
        config.filename = self.filename
        config['LastStat'] = {}
        config['LastStat']['LastLinkQuality'] = str(self.LastStat.LastLinkQuality)
        config['LastStat']['LastSeen'] = str(self.LastStat.LastSeen)
        config['LastStat']['LastHistoryIndex'] = str(self.LastStat.LastHistoryIndex)
        config['LastStat']['LastCurrentWeatherTime'] = str(self.LastStat.LastCurrentWeatherTime)
        config['LastStat']['LastHistoryDataTime'] = str(self.LastStat.LastHistoryDataTime)
        config['LastStat']['LastConfigTime'] = str(self.LastStat.LastConfigTime)
        config.write()

    def writeSettings(self):
        self.filename = CFGFILE
        config = ConfigObj(self.filename)
        config.filename = self.filename
        config['Settings'] = {}
        config['Settings']['DeviceID'] = str(self.Settings.DeviceId)
        config.write()

    def writeDataStore(self):
        self.filename = CFGFILE
        config = ConfigObj(self.filename)
        config.filename = self.filename
        config['DataStore'] = {}
        config['DataStore']['TransceiverSerNo'] = self.TransceiverSerNo
        config.write()

    def getDeviceConfig(self,result):
        logdbg('getDeviceConfig')

    def getTransmissionFrequency(self):
        self.filename = CFGFILE
        config = ConfigObj(self.filename)
        config.filename = self.filename
        try:
            self.TransceiverSettings.TransmissionFrequency = int(config['TransceiverSettings']['TransmissionFrequency'])
        except:
            pass
        logdbg("TransceiverSettings.TransmissionFrequency=%x" % self.TransceiverSettings.TransmissionFrequency)
        return self.TransceiverSettings.TransmissionFrequency

    def setTransmissionFrequency(self,val):
        self.filename = CFGFILE
        config = ConfigObj(self.filename)
        config.filename = self.filename
        config['TransceiverSettings'] = {}
        config['TransceiverSettings']['TransmissionFrequency'] = val
        config.write()
        if val == ETransmissionFrequency.tfEuropean:
            self.TransceiverSettings.Frequency = ETransmissionFrequency.tfEuropeanFreq
        else:
            self.TransceiverSettings.Frequency = ETransmissionFrequency.tfUSFreq

    def getDeviceId(self):
        self.filename = CFGFILE
        config = ConfigObj(self.filename)
        config.filename = self.filename
        try:
            self.Settings.DeviceId = int(config['Settings']['DeviceID'])
        except:
            pass
        logdbg("Settings.DeviceId=%x" % self.Settings.DeviceId)
        return self.Settings.DeviceId

    def setDeviceId(self,val):
        logdbg("setDeviceID to %x" % val)
        self.Settings.DeviceId = val
        self.writeSettings()

    def getFlag_FLAG_TRANSCEIVER_SETTING_CHANGE(self):    # <4>
        flag = BitHandling.testBit(self.Flags, 4)
        logdbg('FLAG_TRANSCIEVER_SETTING_CHANGE=%s' % flag)
        #std::bitset<5>::at(thisa->Flags, &result, 4u);
        return flag

    def getFlag_FLAG_FAST_CURRENT_WEATHER(self):        # <2>
        flag = BitHandling.testBit(self.Flags, 2)
        logdbg('FLAG_FAST_CURRENT_WEATHER=%s' % flag)
        #return self.Flags_FLAG_SERVICE_RUNNING
        #std::bitset<5>::at(thisa->Flags, &result, 2u);
        return flag

    def getFlag_FLAG_TRANSCEIVER_PRESENT(self):        # <0>
        flag = BitHandling.testBit(self.Flags, 0)
        logdbg("FLAG_TRANSCEIVER_PRESENT=%s" % flag)
        #return self.Flags_FLAG_TRANSCEIVER_PRESENT
        return flag

    def getFlag_FLAG_SERVICE_RUNNING(self):            # <3>
        flag = BitHandling.testBit(self.Flags, 3)
        logdbg('FLAG_SERVICE_RUNNING=%s' % flag)
        #return self.Flags_FLAG_SERVICE_RUNNING
        return flag

    def setFlag_FLAG_TRANSCEIVER_SETTING_CHANGE(self,val):    # <4>
        logdbg('set FLAG_TRANSCEIVER_SETTING_CHANGE to %s' % val)
        #std::bitset<5>::set(thisa->Flags, 4u, val);
        self.Flags = BitHandling.setBitVal(self.Flags,4,val)

    def setFlag_FLAG_FAST_CURRENT_WEATHER(self,val):    # <2>
        logdbg('set FLAG_FAST_CURRENT_WEATHER to %s' % val)
        #std::bitset<5>::set(thisa->Flags, 2u, val);
        self.Flags = BitHandling.setBitVal(self.Flags,2,val)

    def setFlag_FLAG_TRANSCEIVER_PRESENT(self,val):        # <0>
        logdbg('set FLAG_TRANSCEIVER_PRESENT to %s' % val)
        #std::bitset<5>::set(thisa->Flags, 0, val);
        self.Flags_FLAG_TRANSCEIVER_PRESENT = val
        self.Flags = BitHandling.setBitVal(self.Flags,0,val)

    def setFlag_FLAG_SERVICE_RUNNING(self,val):        # <3>
        logdbg('set FLAG_SERVICE_RUNNING to %s' % val)
        #std::bitset<5>::set(thisa->Flags, 3u, val);
        self.Flags_FLAG_SERVICE_RUNNING = val
        self.Flags = BitHandling.setBitVal(self.Flags,3,val)

    def setLastLinkQuality(self,Quality):
        if Quality < 90: #lh
            logdbg("setLastLinkQuality: Quality=%d" % Quality)
        self.LastStat.LastLinkQuality = Quality
        self.writeLastStat()

    def setLastSeen(self,time):
        logdbg("setLastSeen: time=%s" % time)
        self.LastStat.LastSeen = time
        self.writeLastStat()

    def getLastSeen(self):
        logdbg("getLastSeen: LastSeen=%d" % self.LastStat.LastSeen)
        return self.LastStat.LastSeen

    def setLastBatteryStatus(self, BatteryStat):
        logdbg('setLastBatteryStatus')
        logdbg("Battery 3=%d 0=%d 1=%d 2=%d" % (BitHandling.testBit(BatteryStat,3),BitHandling.testBit(BatteryStat,0),BitHandling.testBit(BatteryStat,1),BitHandling.testBit(BatteryStat,2)))
        self.LastStat.LastBatteryStatus = BatteryStat

    def setCurrentWeather(self,Data):
        logdbg('setCurrentWeather')
        self.CurrentWeather = Data

    def addHistoryData(self,Data):
        logdbg('addHistoryData')
        self.HistoryData = Data

    def getHistoryData(self,clear):
        logdbg('getHistoryData')
        self.Request.Lock.acquire()
        History = copy.copy(self.HistoryData)
        self.Request.Lock.release()
        return History
    
    def requestNotify(self):
        logdbg('requestNotify: not implemented')
#ATL::CStringT<char_ATL::StrTraitATL<char_ATL::ChTraitsCRT<char>>>::CStringT<char_ATL::StrTraitATL<char_ATL::ChTraitsCRT<char>>>(
#    &FuncName,
#    "void __thiscall CDataStore::requestNotify(void) const");
#v6 = 0;
#ATL::CStringT<char_ATL::StrTraitATL<char_ATL::ChTraitsCRT<char>>>::CStringT<char_ATL::StrTraitATL<char_ATL::ChTraitsCRT<char>>>(
#    &Name,
#    "Request->Lock");
#LOBYTE(v6) = 1;
#CScopedLock::CScopedLock(&lock, &thisa->Request->Lock, &Name, &FuncName);
#LOBYTE(v6) = 3;
#ATL::CStringT<char_ATL::StrTraitATL<char_ATL::ChTraitsCRT<char>>>::_CStringT<char_ATL::StrTraitATL<char_ATL::ChTraitsCRT<char>>>(&Name);
#LOBYTE(v6) = 4;
#ATL::CStringT<char_ATL::StrTraitATL<char_ATL::ChTraitsCRT<char>>>::_CStringT<char_ATL::StrTraitATL<char_ATL::ChTraitsCRT<char>>>(&FuncName);
#boost::interprocess::interprocess_condition::notify_all(&thisa->Request->CondFinish);
#v6 = -1;
#self.Request.CondFinish.notifyAll()
#CScopedLock::_CScopedLock(&lock);

    def setLastCurrentWeatherTime(self,time):
        logdbg("setLastCurrentWeatherTime: time=%s" % (time))
        self.LastStat.LastCurrentWeatherTime = time
        self.writeLastStat()

    def setLastHistoryDataTime(self,time):
        logdbg("setLastHistoryDataTime: time=%s" % time)
        self.LastStat.LastHistoryDataTime = time
        self.writeLastStat()

    def setLastConfigTime(self,time):
        logdbg("setLastConfigTime: time=%s" % time)
        self.LastStat.LastConfigTime = time
        self.writeLastStat()

    def getBufferCheck(self):
        logdbg("getBufferCheck: self.BufferCheck=%x" % self.BufferCheck)
        return self.BufferCheck

    def setBufferCheck(self,val):
        logdbg("setBufferCheck: self.BufferCheck=%x" % val)
        self.BufferCheck = val

    def operator(self):
        logdbg('operator')
        return (self.Guards
                and self.HistoryData
                and self.Flags
                and self.Settings
                and self.TransceiverSettings
                and self.WeatherClubSettings
                and self.LastSeen
                and self.CurrentWeather
                and self.DeviceConfig
                and self.FrontEndConfig
                and self.LastStat
                and self.Request
                and self.LastHistTimeStamp
                and self.BufferCheck);

    def getDeviceRegistered(self):
        logdbg("self.Settings.DeviceRegistered=%x" % self.Settings.DeviceRegistered)
        return self.Settings.DeviceRegistered

    def setDeviceRegistered(self,registered):
        logdbg("Registered=%i" % registered)
        self.Settings.DeviceRegistered = registered;

    def getRequestType(self):
#        logdbg("Request.Type=%d" % self.Request.Type)
        return self.Request.Type

    def setRequestType(self, requesttype):
        logdbg("Request.Type=%d" % requesttype)
        self.Request.Type = requesttype
            
    def getRequestState(self):
        logdbg("Request.State=%d" % self.Request.State)
        return self.Request.State

    def getPreambleDuration(self):
        logdbg("Settings.PreambleDuration=%d" % self.Settings.PreambleDuration)
        return self.Settings.PreambleDuration

    def getRegisterWaitTime(self):
        logdbg("Settings.RegisterWaitTime=%d" % self.Settings.RegisterWaitTime)
        return self.Settings.RegisterWaitTime

    def setRequestState(self,state):
        logdbg("state=%x" % state)
        self.Request.State = state;

    def getCommModeInterval(self):
        logdbg("getCommModeInterval=%x" % self.Settings.CommModeInterval)
        return self.Settings.CommModeInterval

    def setCommModeInterval(self,val):
        logdbg("CommModeInterval=%x" % val)
        self.Settings.CommModeInterval = val

    def setTransceiverID(self,tid):
        if tid != None:
            if self.Settings.TransceiverID != None and self.Settings.TransceiverID != tid:
                self.Settings.TransceiverIdChanged = 1
                self.Settings.TransceiverID = tid
        logdbg("self.Settings.TransceiverID=%x" % self.Settings.TransceiverID)

    def setOutstandingHistorySets(self,val):
        logdbg("setOutstandingHistorySets=%d" % val)
        self.LastStat.OutstandingHistorySets = val
        pass

    def setTransceiverSerNo(self,inp):
        logdbg("TransceiverSerialNumber=%s" % inp)
        self.TransceiverSerNo = inp
        self.writeDataStore()

    def getTransceiverSerNo(self):
        logdbg("getTransceiverSerNo=%s" % self.TransceiverSerNo)
        return self.TransceiverSerNo

    def setLastHistoryIndex(self,val):
        self.LastStat.LastHistoryIndex = val
        logdbg("self.LastStat.LastHistoryIndex=%x" % self.LastStat.LastHistoryIndex)
        self.writeLastStat()

    def getLastHistoryIndex(self):
        logdbg("LastHistoryIndex=%x" % self.LastStat.LastHistoryIndex)
        return self.LastStat.LastHistoryIndex

    def getCurrentWeather(self,Weather,TimeOut):
        logdbg("timeout=%d DeviceRegistered=%d" % (TimeOut, self.getDeviceRegistered() ) )
        #if ( CSingleInstance::IsRunning(this) && CDataStore::getFlag<0>(thisa) && CDataStore::getDeviceRegistered(thisa) )
        if self.getFlag_FLAG_TRANSCEIVER_PRESENT() and self.getDeviceRegistered():
            self.Request.Type = ERequestType.rtGetCurrent;
            self.Request.State = 0;
            self.Request.TTL = 90000;

            try:
                self.Request.CondFinish.acquire()
            except:
                pass
            if self.Request.CondFinish.wait(timedelta(milliseconds=TimeOut).seconds):
                self.Request.Type = ERequestType.rtINVALID #6;
                self.Request.State = ERequestState.rsINVALID #8;
            else:
                self.Request.Type = ERequestType.rtINVALID #6;
                self.Request.State = ERequestState.rsINVALID #8;
            self.Request.CondFinish.release()
        else:
            logerr("getCurrentWeather - warning: flag False or getDeviceRegistered false")

    def getHistory(self,TimeOut):
        logdbg("CDataStore::getHistory")
        #if ( CSingleInstance::IsRunning(this) && CDataStore::getFlag<0>(thisa) && CDataStore::getDeviceRegistered(thisa) )
        if self.getFlag_FLAG_TRANSCEIVER_PRESENT() and self.getDeviceRegistered():
            self.Request.Type = ERequestType.rtGetHistory;
            self.Request.State = 0;
            self.Request.TTL = 90000;

            try:
                self.Request.CondFinish.acquire()
            except:
                pass
            if self.Request.CondFinish.wait(timedelta(milliseconds=TimeOut).seconds):
                self.Request.Type = ERequestType.rtINVALID #6;
                self.Request.State = ERequestState.rsINVALID #8;
                #CDataStore::getHistoryData(thisa, History, 1);
                #        v23 = 0;
                #        v30 = -1;
            else:
                self.Request.Type = ERequestType.rtINVALID #6;
                self.Request.State = ERequestState.rsINVALID #8;
                #        v24 = 1;
                #        v30 = -1;
            self.Request.CondFinish.release()

    def getConfig(self):
        logdbg("getConfig")
        #if ( CSingleInstance::IsRunning(this) && CDataStore::getFlag<0>(thisa) && CDataStore::getDeviceRegistered(thisa) )
        if self.getFlag_FLAG_TRANSCEIVER_PRESENT() and self.getDeviceRegistered():
            self.Request.Type = ERequestType.rtGetConfig;
            self.Request.State = 0;
            self.Request.TTL = 90000;
        else:
            logerr("getConfig - warning: flag False or getDeviceRegistered false")

    def setConfig(self):
        logdbg("setConfig")
        #if ( CSingleInstance::IsRunning(this) && CDataStore::getFlag<0>(thisa) && CDataStore::getDeviceRegistered(thisa) )
        if self.getFlag_FLAG_TRANSCEIVER_PRESENT() and self.getDeviceRegistered():
            self.Request.Type = ERequestType.rtSetConfig;
            self.Request.State = 0;
            self.Request.TTL = 90000;
        else:
            logerr("setConfig - warning: flag False or getDeviceRegistered false")

    def setTime(self):
        logdbg("CDataStore::setTime")
        #if ( CSingleInstance::IsRunning(this) && CDataStore::getFlag<0>(thisa) && CDataStore::getDeviceRegistered(thisa) )
        if self.getFlag_FLAG_TRANSCEIVER_PRESENT() and self.getDeviceRegistered():
            self.Request.Type = ERequestType.rtSetTime;
            self.Request.State = 0;
            self.Request.TTL = 90000;
            self.shid.setSleep(0.020,0010)
        else:
            logerr("setTime - warning: flag False or getDeviceRegistered false")

    def requestTick(self):
        logdbg('requestTick')
        if self.Request.Type != 6:
            self.Request.TTL -= 1
            if not self.Request.TTL:
                self.Request.Type = 6
                self.Request.State = 8
                logerr("internal timeout, request aborted")

class sHID(object):
    """USB driver abstraction"""

    def __init__(self):
        logdbg('sHID_init')
        self.devh = None
        self.debug = 0
        self.timeout = 1000
        self.prev_data = [0]*0x131
        self.PollCount = 0
        self.LastState = 0
        self.FirstSleep = 0.020
        self.SecondSleep = 0.020

    def open(self, vid=0x6666, pid=0x5555):
        device = self._find_device(vid, pid)
        if device is None:
            logcrt('Cannot find USB device with Vendor=0x%04x ProdID=0x%04x' %
                   (vid, pid))
            raise weewx.WeeWxIOError('Unable to find USB device')
        self._open_device(device)

    def close(self):
        self._close_device()

    def _find_device(self, vid, pid):
        for bus in usb.busses():
            for device in bus.devices:
                if device.idVendor == vid and device.idProduct == pid:
                    return device
        return None

    def _open_device(self, device, interface=0, configuration=1):
        self._device = device
        self._configuration = device.configurations[0]
        self._interface = self._configuration.interfaces[0][0]
        self._endpoint = self._interface.endpoints[0]
        self.devh = device.open()
        loginf('manufacturer: %s' % self.devh.getString(device.iManufacturer,30))
        loginf('product: %s' % self.devh.getString(device.iProduct,30))
        loginf('interface: %d' % self._interface.interfaceNumber)

        # detach any old claimed interfaces
        try:
            self.devh.detachKernelDriver(self._interface.interfaceNumber)
        except:
            pass

        # FIXME: this seems to be specific to ws28xx?
        usbWait = 0.05 #lh was: 0.5
        self.devh.getDescriptor(0x1, 0, 0x12)
        time.sleep(usbWait)
        self.devh.getDescriptor(0x2, 0, 0x9)
        time.sleep(usbWait)
        self.devh.getDescriptor(0x2, 0, 0x22)
        time.sleep(usbWait)

        # attempt to claim the interface
        try:
            if platform.system() is 'Windows':
                loginf('set USB device configuration to %d' % configuration)
                self.devh.setConfiguration(configuration)
            loginf('claim USB interface %d' % interface)
            self.devh.claimInterface(interface)
            self.devh.setAltInterface(interface)
        except usb.USBError, e:
            self._close_device()
            raise weewx.WeeWxIOError(e)

        # FIXME: this seems to be specific to ws28xx?
        # FIXME: check return value
        self.devh.controlMsg(
            usb.TYPE_CLASS + usb.RECIP_INTERFACE,
            0x000000a, [], 0x0000000, 0x0000000, 1000);
        time.sleep(0.05) #lh 0.05 was: 0.3
        self.devh.getDescriptor(0x22, 0, 0x2a9)
        time.sleep(usbWait)

    def _close_device(self):
        try:
            self.devh.releaseInterface()
        except:
            pass
        try:
            self.devh.detachKernelDriver(self._interface.interfaceNumber)
        except:
            pass

    def getPollCount(self):
        return self.PollCount
    
    def setSleep(self, firstsleep, secondsleep):
        self.FirstSleep = firstsleep
        self.SecondSleep = secondsleep
         
    def setTX(self):
        buf = [0]*0x15
        buf[0] = 0xd1;
        self.dump('setTX', buf)
        try:
            self.devh.controlMsg(usb.TYPE_CLASS + usb.RECIP_INTERFACE,
                                 request=0x0000009,
                                 buffer=buf,
                                 value=0x00003d1,
                                 index=0x0000000,
                                 timeout=self.timeout)
            result = 1
        except:
            result = 0
        return result

    def setRX(self):
        buf = [0]*0x15
        buf[0] = 0xD0;
        self.dump('setRX', buf)
        try:
            self.devh.controlMsg(usb.TYPE_CLASS + usb.RECIP_INTERFACE,
                                 request=0x0000009,
                                 buffer=buf,
                                 value=0x00003d0,
                                 index=0x0000000,
                                 timeout=self.timeout)
            result = 1
        except:
            result = 0
        return result

    def getState(self,StateBuffer): 
        self.PollCount = 0
        found = False
        time.sleep(self.FirstSleep) # initial wait after setRX
        loginf('FirstSleep=%1.3f SecondSleep=%1.3f' % (self.FirstSleep, self.SecondSleep))
        while not found:
            try:
                buf = self.devh.controlMsg(requestType=usb.TYPE_CLASS |
                                       usb.RECIP_INTERFACE | usb.ENDPOINT_IN,
                                       request=usb.REQ_CLEAR_FEATURE,
                                       buffer=0x0a,
                                       value=0x00003de,
                                       index=0x0000000,
                                       timeout=self.timeout)
                self.PollCount += 1 # count the received states
                if buf[1] == 0x16:
                    if self.LastState != 123 : #0x16 : # we are only interested in 0xde16 messages
                        self.LastState = 0x16
                        StateBuffer[0]=[0]*0x2
                        StateBuffer[0][0]=buf[1]
                        StateBuffer[0][1]=buf[2]
                        found = True # jump out while loop
                        result = 1
                    else:
                        if self.pollcount > 200:
                            self.LastState = 0
                        time.sleep(self.SecondSleep) #initial wait after getState 
                else:
                    self.LastState = 0
                    time.sleep(self.SecondSleep) #initial wait after getState
            except:
                logerr('exception getState')
                time.sleep(self.SecondSleep)
                if self.debug == 1:
                    buf[1]=0x14
                    StateBuffer[0]=[0]*0x2
                    StateBuffer[0][0]=buf[1]
                    StateBuffer[0][1]=buf[2]
                found = True # jump out while loop
                result = 0
        return result

    def readConfigFlash(self,addr,numBytes,data):
        if numBytes <= 512:
            while ( numBytes ):
                buf=[0xcc]*0x0f #0x15
                buf[0] = 0xdd
                buf[1] = 0x0a
                buf[2] = (addr >>8)  & 0xFF;
                buf[3] = (addr >>0)  & 0xFF;
                self.dump('readConfigFlash>', buf)
                try:
                    # FIXME: check return value
                    self.devh.controlMsg(usb.TYPE_CLASS + usb.RECIP_INTERFACE,
                                         request=0x0000009,
                                         buffer=buf,
                                         value=0x00003dd,
                                         index=0x0000000,
                                         timeout=self.timeout)
                    result = 1
                    USBHardware.dumpBuf('rcfo', buf, 4)
                except:
                    result = 0

                try:
                    buf = self.devh.controlMsg(requestType=usb.TYPE_CLASS |
                                               usb.RECIP_INTERFACE |
                                               usb.ENDPOINT_IN,
                                               request=usb.REQ_CLEAR_FEATURE,
                                               buffer=0x15,
                                               value=0x00003dc,
                                               index=0x0000000,
                                               timeout=self.timeout)
                    result = 1
                    USBHardware.dumpBuf('rcfi', buf, 20)
                except:
                    result = 0
                    if addr == 0x1F5 and self.debug == 1: #//fixme #debugging... without device
                        logdbg("sHID::readConfigFlash -emulated 0x1F5")
                        buf=[0xdc,0x0a,0x01,0xf5,0x00,0x01,0x78,0xa0,0x01,0x01,0x0c,0x0a,0x0a,0x00,0x41,0xff,0xff,0xff,0xff,0xff,0x00]

                    if addr == 0x1F9 and self.debug == 1: #//fixme #debugging... without device
                        logdbg("sHID::readConfigFlash -emulated 0x1F9")
                        buf=[0xdc,0x0a,0x01,0xf9,0x01,0x01,0x0c,0x0a,0x0a,0x00,0x41,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0x00]
                    if self.debug != 1:
                        return 0;

                new_data=[0]*0x15
                if ( numBytes < 16 ):
                    for i in xrange(0, numBytes):
                        new_data[i] = buf[i+4];
                    numBytes = 0;
                else:
                    for i in xrange(0, 16):
                        new_data[i] = buf[i+4];
                    numBytes -= 16;
                    addr += 16;
                self.dump('readConfigFlash<', buf)

            result = 1;
        else:
            result = 0;

        data[0] = new_data
        return result

    def setState(self,state):
        buf = [0]*0x15
        buf[0] = 0xd7;
        buf[1] = state;
        self.dump('setState', buf)
        try:
            self.devh.controlMsg(usb.TYPE_CLASS + usb.RECIP_INTERFACE,
                                 request=0x0000009,
                                 buffer=buf,
                                 value=0x00003d7,
                                 index=0x0000000,
                                 timeout=self.timeout)
            result = 1
        except:
            result = 0
        return result

    def setFrame(self,data,numBytes):

#    00000000: d5 00 09 f0 f0 03 00 32 00 3f ff ff 00 00 00 00
#    00000000: d5 00 0c 00 32 c0 00 8f 45 25 15 91 31 20 01 00
#    00000000: d5 00 09 00 32 00 06 c1 00 3f ff ff 00 00 00 00
#    00000000: d5 00 09 00 32 01 06 c1 00 3f ff ff 00 00 00 00
#    00000000: d5 00 0c 00 32 c0 06 c1 47 25 15 91 31 20 01 00
#    00000000: d5 00 09 00 32 00 06 c1 00 30 01 a0 00 00 00 00
#    00000000: d5 00 09 00 32 02 06 c1 00 30 01 a0 00 00 00 00
#    00000000: d5 00 30 00 32 40 64 33 53 04 00 00 00 00 00 00
#    00000000: d5 00 09 00 32 00 06 ab 00 30 01 a0 00 00 00 00
#    00000000: d5 00 09 00 32 00 04 d0 00 30 01 a0 00 00 00 00
#    00000000: d5 00 09 00 32 02 04 d0 00 30 01 a0 00 00 00 00
#    00000000: d5 00 30 00 32 40 64 32 53 04 00 00 00 00 00 00
#    00000000: d5 00 09 00 32 00 04 cf 00 30 01 a0 00 00 00 00

        buf = [0]*0x111 
        buf[0] = 0xd5;
        buf[1] = numBytes >> 8;
        buf[2] = numBytes;
        for i in xrange(0, numBytes):
            buf[i+3] = data[i]
        self.dump('setFrame', buf)
        try:
            self.devh.controlMsg(usb.TYPE_CLASS + usb.RECIP_INTERFACE,
                                 request=0x0000009,
                                 buffer=buf,
                                 value=0x00003d5,
                                 index=0x0000000,
                                 timeout=self.timeout)
            result = 1
        except:
            result = 0
        return result

    def getFrame(self,data,numBytes):
        try:
            buf = self.devh.controlMsg(requestType=usb.TYPE_CLASS |
                                       usb.RECIP_INTERFACE |
                                       usb.ENDPOINT_IN,
                                       request=usb.REQ_CLEAR_FEATURE,
                                       buffer=0x111,
                                       value=0x00003d6,
                                       index=0x0000000,
                                       timeout=self.timeout)
            result = 1
        except:
            result = 0

        new_data=[0]*0x131
        new_numBytes=(buf[1] << 8 | buf[2])& 0x1ff;
        different = 0
        for i in xrange(0, new_numBytes):
            if (different == 0) and (self.prev_data[i+3] != buf[i+3]):
                if (i != 3):
                    different = 1
            new_data[i] = buf[i+3];
            self.prev_data[i+3] = buf[i+3];

        data[0] = new_data
        numBytes[0] = new_numBytes
        self.dump('getFrame', buf)
        if different == 0:
            loginf('getFrame double message')
            return result #2 # data is the same as with previous getFrame (active when return = 2)
        else:
            return result

    def writeReg(self,regAddr,data):
        buf = [0]*0x05
        buf[0] = 0xf0;
        buf[1] = regAddr & 0x7F;
        buf[2] = 0x01;
        buf[3] = data;
        buf[4] = 0x00;
        try:
            self.devh.controlMsg(usb.TYPE_CLASS + usb.RECIP_INTERFACE,
                                 request=0x0000009,
                                 buffer=buf,
                                 value=0x00003f0,
                                 index=0x0000000,
                                 timeout=self.timeout)
            result = 1
        except:
            result = 0
        return result

    def execute(self,command):
        buf = [0]*0x0f #*0x15
        buf[0] = 0xd9;
        buf[1] = command;
        self.dump('execute', buf)
        try:
            self.devh.controlMsg(usb.TYPE_CLASS + usb.RECIP_INTERFACE,
                                 request=0x0000009,
                                 buffer=buf,
                                 value=0x00003d9,
                                 index=0x0000000,
                                 timeout=self.timeout)
            result = 1
            USBHardware.dumpBuf('exe ', buf, 20) 
        except:
            result = 0
        return result

    def setPreamblePattern(self,pattern):
        buf = [0]*0x15
        buf[0] = 0xd8;
        buf[1] = pattern
        self.dump('setPreamblePattern', buf)
        try:
            self.devh.controlMsg(usb.TYPE_CLASS + usb.RECIP_INTERFACE,
                                 request=0x0000009,
                                 buffer=buf,
                                 value=0x00003d8,
                                 index=0x0000000,
                                 timeout=self.timeout)
            result = 1
            USBHardware.dumpBuf('spp ', buf, 20) 
        except:
            result = 0
        return result

    def dump(self, cmd, buf):
        actTime = datetime.now()
        strbuf = ""
        buflen = 0
        for i in buf:
            buflen += 1
            if buflen <= (15):
                strbuf += str("%.2x " % i)
        if (cmd=='getFrame' or cmd=='setFrame'):
            loginf("%s %s: %s" % (actTime, cmd, strbuf))

class CCommunicationService(object):

    AX5051RegisterNames_map = dict()

    class AX5051RegisterNames:
        REVISION         = 0x0
        SCRATCH          = 0x1
        POWERMODE        = 0x2
        XTALOSC          = 0x3
        FIFOCTRL         = 0x4
        FIFODATA         = 0x5
        IRQMASK          = 0x6
        IFMODE           = 0x8
        PINCFG1          = 0x0C
        PINCFG2          = 0x0D
        MODULATION       = 0x10
        ENCODING         = 0x11
        FRAMING          = 0x12
        CRCINIT3         = 0x14
        CRCINIT2         = 0x15
        CRCINIT1         = 0x16
        CRCINIT0         = 0x17
        FREQ3            = 0x20
        FREQ2            = 0x21
        FREQ1            = 0x22
        FREQ0            = 0x23
        FSKDEV2          = 0x25
        FSKDEV1          = 0x26
        FSKDEV0          = 0x27
        IFFREQHI         = 0x28
        IFFREQLO         = 0x29
        PLLLOOP          = 0x2C
        PLLRANGING       = 0x2D
        PLLRNGCLK        = 0x2E
        TXPWR            = 0x30
        TXRATEHI         = 0x31
        TXRATEMID        = 0x32
        TXRATELO         = 0x33
        MODMISC          = 0x34
        FIFOCONTROL2     = 0x37
        ADCMISC          = 0x38
        AGCTARGET        = 0x39
        AGCATTACK        = 0x3A
        AGCDECAY         = 0x3B
        AGCCOUNTER       = 0x3C
        CICDEC           = 0x3F
        DATARATEHI       = 0x40
        DATARATELO       = 0x41
        TMGGAINHI        = 0x42
        TMGGAINLO        = 0x43
        PHASEGAIN        = 0x44
        FREQGAIN         = 0x45
        FREQGAIN2        = 0x46
        AMPLGAIN         = 0x47
        TRKFREQHI        = 0x4C
        TRKFREQLO        = 0x4D
        XTALCAP          = 0x4F
        SPAREOUT         = 0x60
        TESTOBS          = 0x68
        APEOVER          = 0x70
        TMMUX            = 0x71
        PLLVCOI          = 0x72
        PLLCPEN          = 0x73
        PLLRNGMISC       = 0x74
        AGCMANUAL        = 0x78
        ADCDCLEVEL       = 0x79
        RFMISC           = 0x7A
        TXDRIVER         = 0x7B
        REF              = 0x7C
        RXMISC           = 0x7D

    def __init__(self, cfgfn):
        logdbg('CCommunicationService_init %s' % cfgfn) 
        self.RepeatCount = 0
        self.RepeatSize = 0
        self.RepeatInterval = None
        self.RepeatTime = datetime.now() #ptime

        self.Regenerate = 0
        self.GetConfig = 0

        self.TimeSent = 0
        self.TimeUpdate = 0
        self.TimeUpdateComplete = 0

        self.DataStore = CDataStore(cfgfn)
        self.running = True
        self.shid = sHID()

        self.TimeDifSec = 0
        self.DifHis = 0
        self.shid = sHID()

    def buildFirstConfigFrame(self,Buffer):
        loginf("buildFirstConfigFrame")
        USBHardware.dumpBuf('1stI', Buffer[0], 0x6)
        newBuffer = [0]
        newBuffer[0] = [0]*9
        DeviceCS = Buffer[0][5] | (Buffer[0][4] << 8);
        self.DataStore.WeatherStationConfig.setDeviceCS(DeviceCS)
        ComInt = self.DataStore.getCommModeInterval();
        HistoryAddress = 0xFFFFFF
        newBuffer[0][0] = 0xF0
        newBuffer[0][1] = 0xF0
        newBuffer[0][2] = 3
        newBuffer[0][3] = (DeviceCS >> 8 ) & 0xFF
        newBuffer[0][4] = (DeviceCS >> 0 ) & 0xFF
        newBuffer[0][5] = (ComInt >> 4) & 0xFF ;
        newBuffer[0][6] = (HistoryAddress >> 16) & 0x0F | 16 * (ComInt & 0xF);
        newBuffer[0][7] = (HistoryAddress >> 8 ) & 0xFF # BYTE1(HistoryAddress);
        newBuffer[0][8] = (HistoryAddress >> 0 ) & 0xFF
        USBHardware.dumpBuf('1stO', newBuffer[0], 0x9)
        Buffer[0]=newBuffer[0]
        Length = 0x09
        return Length

    def buildConfigFrame(self,Buffer):
        logdbg("buildConfigFrame")
        newBuffer = [0]
        newBuffer[0] = [0]*48
        cfgBuffer = [0]
        cfgBuffer[0] = [0]*44
        Changed = self.DataStore.WeatherStationConfig.testConfigChanged(cfgBuffer)        
        if Changed:            
            newBuffer[0][0] = Buffer[0][0]
            newBuffer[0][1] = Buffer[0][1]
            newBuffer[0][2] = 0x40 # change this value if we (temporary) won't store config
            newBuffer[0][3] = Buffer[0][3]
            for i in xrange(0,44):
                newBuffer[0][i+4] = cfgBuffer[0][i]       
            Buffer[0]=newBuffer[0]
            Length = 48 #0x30
        else: # current config not up to date; don't write yet
            Length = 0
        time = datetime.now()
        loginf('buildConfigFrame')
        ###USBHardware.dumpBuf('Out ', newBuffer[0], 0x30)
        return Length    

    def buildTimeFrame(self,Buffer,checkMinuteOverflow):
        logdbg("checkMinuteOverflow=%x" % checkMinuteOverflow)

        DeviceCS = self.DataStore.WeatherStationConfig.getDeviceCS()
        now = time.time()
        tm = time.localtime(now)
        #tu = time.gmtime(now)

        newBuffer=[0]
        newBuffer[0]=Buffer[0]
        Second = tm[5]
        if Second > 59:
            Second = 0 # I don't know if La Crosse support leap seconds...
        if ( checkMinuteOverflow and (Second <= 5 or Second >= 55) ):
            if ( Second < 55 ):
                Second = 6 - Second
            else:
                Second = 60 - Second + 6;
            loginf('set ComInt to %s s' % Second)
            HistoryIndex = self.DataStore.getLastHistoryIndex();
            Length = self.buildACKFrame(newBuffer, 0, DeviceCS, HistoryIndex, Second);
            Buffer[0]=newBuffer[0]
        else:
            #00000000: d5 00 0c 00 32 c0 00 8f 45 25 15 91 31 20 01 00
            #00000000: d5 00 0c 00 32 c0 06 c1 47 25 15 91 31 20 01 00
            #                             3  4  5  6  7  8  9 10 11
            newBuffer[0][2] = 0xc0
            newBuffer[0][3] = (DeviceCS >>8)  & 0xFF #BYTE1(DeviceCS);
            newBuffer[0][4] = (DeviceCS >>0)  & 0xFF #DeviceCS;
            newBuffer[0][5] = (tm[5] % 10) + 0x10 * (tm[5] // 10); #sec
            newBuffer[0][6] = (tm[4] % 10) + 0x10 * (tm[4] // 10); #min
            newBuffer[0][7] = (tm[3] % 10) + 0x10 * (tm[3] // 10); #hour
            #DayOfWeek = tm[6] - 1; #ole from 1 - 7 - 1=Sun... 0-6 0=Sun
            DayOfWeek = tm[6];      #py  prom 0 - 6 - 0=Mon
            #if ( DayOfWeek == 1 ): # this was for OLE::Time
            #    DayOfWeek = 7;  # this was for OLE::Time
            newBuffer[0][8] = DayOfWeek % 10 + 0x10 *  (tm[2] % 10)          #DoW + Day
            newBuffer[0][9] =  (tm[2] // 10) + 0x10 *  (tm[1] % 10)          #day + month
            newBuffer[0][10] = (tm[1] // 10) + 0x10 * ((tm[0] - 2000) % 10)  #month + year
            newBuffer[0][11] = (tm[0] - 2000) // 10                          #year
            self.Regenerate = 1
            self.TimeSent = 1
            Buffer[0]=newBuffer[0]
            Length = 0x0c
            loginf('WS SetTime - Send time to WS')
            USBHardware.dumpBuf('Time', newBuffer[0], 0x0c)
            self.shid.setSleep(0.100,0.005) #80,5
        return Length

    def buildACKFrame(self,Buffer, Action, DeviceCS, HistoryIndex, ComInt):
        logdbg("Action=%x, DeviceCS=%04x, HistoryIndex=%04x, ComInt=%x" % (Action, DeviceCS, HistoryIndex, ComInt))
        newBuffer = [0]
        newBuffer[0] = [0]*9
        for i in xrange(0,2):
            newBuffer[0][i] = Buffer[0][i]
            # Forece action GetCurrent when LastCurrentWeatherTime is more than 30 seconds ago
            if (Action != 5) and datetime.now() - self.DataStore.LastStat.LastCurrentWeatherTime >= timedelta(seconds=30):
                loginf('Requested action=%s: Force action GetCurrent' % Action)
                Action = 5
        if Action == 0: # never ask for historical record
            loginf('Action 0 set to 5')
            Action = 5
        newBuffer[0][2] = Action & 0xF;
        if ( HistoryIndex >= 0x705 ):
            HistoryAddress = 0xffffff
        else:
            if   ( self.DataStore.getBufferCheck() != 1
                   and self.DataStore.getBufferCheck() != 2 ):
                HistoryAddress = 18 * HistoryIndex + 0x1a0
            else:
                if ( HistoryIndex != 0xffff ):
                    HistoryAddress = 18 * (HistoryIndex - 1) + 0x1a0
                else:
                    HistoryAddress = 0x7fe8;
                self.DataStore.setBufferCheck( 2)
        newBuffer[0][3] = (DeviceCS >> 8) &0xFF;
        newBuffer[0][4] = (DeviceCS >> 0) &0xFF;
        if ( ComInt == 0xFFFFFFFF ):
            ComInt = self.DataStore.getCommModeInterval();
        newBuffer[0][5] = (ComInt >> 4) & 0xFF ;
        newBuffer[0][6] = (HistoryAddress >> 16) & 0x0F | 16 * (ComInt & 0xF);
        newBuffer[0][7] = (HistoryAddress >> 8 ) & 0xFF # BYTE1(HistoryAddress);
        newBuffer[0][8] = (HistoryAddress >> 0 ) & 0xFF

        #d5 00 09 f0 f0 03 00 32 00 3f ff ff
        Buffer[0]=newBuffer[0]
        self.Regenerate = 0;
        self.TimeSent = 0;
        return 9

    def handleWsAck(self,Buffer,Length):
        logdbg('handleWsAck')
        self.DataStore.setLastSeen( datetime.now());
        BatteryStat = (Buffer[0][2] & 0xF);
        self.DataStore.setLastBatteryStatus( BatteryStat);
        Quality = Buffer[0][3] & 0x7F;
        self.DataStore.setLastLinkQuality( Quality);
        Length[0] = 0

    def handleConfig(self,Buffer,Length):
        logdbg('handleConfig')
        newBuffer=[0]
        newBuffer[0] = Buffer[0]
        newLength = [0]
        self.DataStore.WeatherStationConfig.CWeatherStationConfig_buf(newBuffer, 4);
        HistoryIndex = self.DataStore.getLastHistoryIndex();
        start = 4
        DeviceCS = newBuffer[0][43+start] | (newBuffer[0][42+start] << 8);
        self.DataStore.WeatherStationConfig.setDeviceCS(DeviceCS)

        pollcount = self.shid.getPollCount()
        loginf('handleConfig pollcount=%s' % pollcount)

        self.DataStore.setLastConfigTime( datetime.now())
        self.DataStore.setRequestType(ERequestType.rtGetCurrent)
        rt = self.DataStore.getRequestType();
        logdbg('request type: %d' % rt)
        if   rt == ERequestType.rtGetCurrent: #rtGetCurrent
            ###self.DataStore.setRequestState( ERequestState.rsFinished); #2
            ###self.DataStore.requestNotify();
            newLength[0] = self.buildACKFrame(newBuffer, 0, DeviceCS, HistoryIndex, 0xFFFFFFFF);
        elif rt == ERequestType.rtGetConfig: #rtGetConfig
            newLength[0] = self.buildACKFrame(newBuffer, 3, DeviceCS, HistoryIndex, 0xFFFFFFFF);
            self.DataStore.setRequestState( ERequestState.rsRunning); #1
        elif rt == ERequestType.rtSetConfig: #rtSetConfig
            newLength[0] = self.buildACKFrame(newBuffer, 2, DeviceCS, HistoryIndex, 0xFFFFFFFF);
            self.DataStore.setRequestState( ERequestState.rsRunning); #1
        elif rt == ERequestType.rtGetHistory: #rtGetHistory
            newLength[0] = self.buildACKFrame(newBuffer, 5, DeviceCS, HistoryIndex, 0xFFFFFFFF);
            self.DataStore.setRequestState( ERequestState.rsRunning); #1
        elif rt == ERequestType.rtSetTime: #rtSetTime
            newLength[0] = self.buildACKFrame(newBuffer, 1, DeviceCS, HistoryIndex, 0xFFFFFFFF);
            self.DataStore.setRequestState( ERequestState.rsRunning); #1
        elif rt == ERequestType.rtINVALID: #rtINVALID
            newLength[0] = self.buildACKFrame(newBuffer, 0, DeviceCS, HistoryIndex, 0xFFFFFFFF); 
        
        Buffer[0] = newBuffer[0]
        Length[0] = newLength[0]

    def handleCurrentData(self,Buffer,Length):
        logdbg('handleCurrentData')
        newBuffer = [0]
        newBuffer[0] = Buffer[0]
        newLength = [0]
        Data = CCurrentWeatherData()
        Data.CCurrentWeatherData_buf(newBuffer, 6);
        self.DataStore.setLastSeen( datetime.now());
        self.DataStore.setLastCurrentWeatherTime( datetime.now())
        BatteryStat = (Buffer[0][2] & 0xF);
        if BatteryStat > 0:
            loginf('LET OP: BatteryStat=%s' % BatteryStat)
        self.DataStore.setLastBatteryStatus( BatteryStat);
        Quality = Buffer[0][3] & 0x7F;
        self.DataStore.setLastLinkQuality( Quality);
        self.DataStore.setCurrentWeather( Data);
        DeviceCS = newBuffer[0][5] | (newBuffer[0][4] << 8);
        self.DataStore.WeatherStationConfig.setDeviceCS(DeviceCS)
        
        pollcount = self.shid.getPollCount()
        loginf('handleCurrentData pollcount=%s' % pollcount)   
        
        cfgBuffer = [0]
        cfgBuffer[0] = [0]*44
        Changed = self.DataStore.WeatherStationConfig.testConfigChanged(cfgBuffer)
        InBufCS = self.DataStore.WeatherStationConfig.getInBufCS()
        #first test on changed config, then test of cuurent connfig has changed
        if (InBufCS == 0) or (InBufCS != DeviceCS): 
            loginf('InBufCS of Weather Station not actual: rtGetConfig')
            self.DataStore.setRequestType(ERequestType.rtGetConfig)
            self.shid.setSleep(0.320,0.005) #325,5r
        elif Changed:
            loginf('OutBufCS of Weather Station changed: rtSetConfig')
            self.DataStore.setRequestType(ERequestType.rtSetConfig)
            self.shid.setSleep(0.380,0.005)
        else:
            self.DataStore.setRequestType(ERequestType.rtGetHistory)
            if self.DifHis > 0:
                self.shid.setSleep(0.380,0.200) # timing for Outstanding History records
            else:
                self.shid.setSleep(5.200,0.020) # timing for Current Data and newest History record    

        HistoryIndex = self.DataStore.getLastHistoryIndex();
        rt = self.DataStore.getRequestType();
        if   rt == ERequestType.rtGetCurrent: #rtGetCurrent
            self.DataStore.setRequestState( ERequestState.rsFinished); #2
            self.DataStore.requestNotify();
            newLength[0] = self.buildACKFrame(newBuffer, 0, DeviceCS, HistoryIndex, 0xFFFFFFFF);
        elif rt == ERequestType.rtGetConfig: #rtGetConfig
            newLength[0] = self.buildACKFrame(newBuffer, 3, DeviceCS, HistoryIndex, 0xFFFFFFFF);
            self.DataStore.setRequestState( ERequestState.rsRunning); #1
        elif rt == ERequestType.rtSetConfig: #rtSetConfig
            newLength[0] = self.buildACKFrame(newBuffer, 2, DeviceCS, HistoryIndex, 0xFFFFFFFF);
            self.DataStore.setRequestState( ERequestState.rsRunning); #1
        elif rt == ERequestType.rtGetHistory: #rtGetHistory
            newLength[0] = self.buildACKFrame(newBuffer, 0, DeviceCS, HistoryIndex, 0xFFFFFFFF);
            self.DataStore.setRequestState( ERequestState.rsRunning); #1
        elif rt == ERequestType.rtSetTime: #rtSetTime
            newLength[0] = self.buildACKFrame(newBuffer, 1, DeviceCS, HistoryIndex, 0xFFFFFFFF);
            self.DataStore.setRequestState( ERequestState.rsRunning); #1
        elif rt == ERequestType.rtINVALID: #rtINVALID
            newLength[0] = self.buildACKFrame(newBuffer, 0, DeviceCS, HistoryIndex, 0xFFFFFFFF);

        Length[0] = newLength[0]
        Buffer[0] = newBuffer[0]

    def handleHistoryData(self,Buffer,Length):
        logdbg('handleHistoryData')
        newBuffer = [0]
        newBuffer[0] = Buffer[0]
        newLength = [0]
        Data = CHistoryDataSet() #similar to currentwheather as it works ;-)
        Data.CHistoryDataSet_buf(newBuffer, 12)
        DeviceCS = newBuffer[0][5] | (newBuffer[0][4] << 8);
        self.DataStore.WeatherStationConfig.setDeviceCS(DeviceCS)   
        self.DataStore.setLastSeen( datetime.now());
        BatteryStat = (Buffer[0][2] & 0xF);
        self.DataStore.setLastBatteryStatus( BatteryStat);
        Quality = Buffer[0][3] & 0x7F;
        self.DataStore.setLastLinkQuality( Quality);
        LatestHistoryAddres = ((((Buffer[0][6] & 0xF) << 8) | Buffer[0][7]) << 8) | Buffer[0][8];
        ThisHistoryAddres = ((((Buffer[0][9] & 0xF) << 8) | Buffer[0][10]) << 8) | Buffer[0][11];
        LatestHistoryIndex = (LatestHistoryAddres - 415) / 0x12;
        ThisHistoryIndex = (ThisHistoryAddres - 415) / 0x12;

        pollcount = self.shid.getPollCount()
        if ( LatestHistoryIndex >= ThisHistoryIndex ):
            self.DifHis = LatestHistoryIndex - ThisHistoryIndex
        else:
            self.DifHis = LatestHistoryIndex + 1797 - ThisHistoryIndex
            
        # Initially we get 1795 history records from the WS
        # As we don't need them we only read history records when the history is less than 60 (one hour)
        if (self.DifHis > 0) and (self.DifHis < 1790):
            loginf('m_Time          %s  OutstandingHistorySets: %4i - skip reading history - pollcount=%s' % (Data.m_Time, self.DifHis, pollcount)) 
            ThisHistoryIndex = LatestHistoryIndex
            self.DifHis = 0
            self.DataStore.setLastHistoryIndex( ThisHistoryIndex);
        else:    
            if ( ThisHistoryIndex == self.DataStore.getLastHistoryIndex()):
                self.DataStore.setLastHistoryDataTime( datetime.now())
                self.DataStore.setBufferCheck( 0)
            else:
                self.DataStore.addHistoryData(Data);
                self.DataStore.setLastHistoryIndex( ThisHistoryIndex);

        if self.DifHis > 0:
            loginf('m_Time          %s  OutstandingHistorySets: %4i pollcount=%s' % (Data.m_Time, self.DifHis, pollcount)) 

        if ThisHistoryIndex == LatestHistoryIndex:
            self.TimeDifSec = (Data.m_Time - datetime.now()).seconds
            if self.TimeDifSec > 43200:
                self.TimeDifSec = self.TimeDifSec - 86400 +1
            loginf('m_Time          %s      WS clock offset %4s s pollcount=%s' % (Data.m_Time, self.TimeDifSec, pollcount))
        else:
            logdbg('m_Time          %s  No recent historydata' % Data.m_Time)
        
        rt = self.DataStore.getRequestType()
        if   rt == ERequestType.rtGetCurrent: #rtGetCurrent
            newLength[0] = self.buildACKFrame(newBuffer, 5, DeviceCS, ThisHistoryIndex, 0xFFFFFFFF);
            self.DataStore.setRequestState( ERequestState.rsRunning);
        elif rt == ERequestType.rtGetConfig: #rtGetConfig
            newLength[0] = self.buildACKFrame(newBuffer, 3, DeviceCS, ThisHistoryIndex, 0xFFFFFFFF);
            self.DataStore.setRequestState( ERequestState.rsRunning);
        elif rt == ERequestType.rtSetConfig: #rtSetConfig
            newLength[0] = self.buildACKFrame(newBuffer, 2, DeviceCS, ThisHistoryIndex, 0xFFFFFFFF);
            self.DataStore.setRequestState( ERequestState.rsRunning);
        elif rt == ERequestType.rtGetHistory: #rtGetHistory
            newLength[0] = self.buildACKFrame(newBuffer, 0, DeviceCS, ThisHistoryIndex, 0xFFFFFFFF);
        elif rt == ERequestType.rtSetTime: #rtSetTime
            newLength[0] = self.buildACKFrame(newBuffer, 1, DeviceCS, ThisHistoryIndex, 0xFFFFFFFF);
            self.DataStore.setRequestState( ERequestState.rsRunning);
        elif rt == ERequestType.rtINVALID: #rtINVALID
            newLength[0] = self.buildACKFrame(newBuffer, 0, DeviceCS, ThisHistoryIndex, 0xFFFFFFFF);

        Length[0] = newLength[0]
        Buffer[0] = newBuffer[0]

    def handleNextAction(self,Buffer,Length):
        logdbg('handleNextAction')
        newBuffer = [0]
        newBuffer[0] = Buffer[0]
        newLength = [0]
        newLength[0] = Length[0]
        #print "handleNextAction:: Buffer[0] %x" % Buffer[0][0]
        #print "handleNextAction:: Buffer[1] %x" % Buffer[0][1]
        #print "handleNextAction:: Buffer[2] %x (CWeatherStationConfig *)" % (Buffer[0][2] & 0xF)
        rt = self.DataStore.getRequestType()
        HistoryIndex = self.DataStore.getLastHistoryIndex();
        DeviceCS = newBuffer[0][5] | (newBuffer[0][4] << 8);
        self.DataStore.WeatherStationConfig.setDeviceCS(DeviceCS)
        self.DataStore.setLastSeen( datetime.now());
        Quality = Buffer[0][3] & 0x7F;
        self.DataStore.setLastLinkQuality( Quality);
        time = datetime.now()
        if (Buffer[0][2] & 0xF) == 1: #(FirstConfig *)
            newLength[0] = self.buildFirstConfigFrame(newBuffer)
        elif (Buffer[0][2] & 0xF) == 2: #(CWeatherStationConfig *)
            loginf('WS SetConfig - Request Config Set')
            loginf("handleNextAction - Set Config Data")
            self.shid.setSleep(0.380,0.005) #380,5
            newLength[0] = self.buildConfigFrame(newBuffer)
            #newLength[0] = 0
            #    v16 = CDataStore::getFrontEndConfig( &result);
            #    Data = v16;
#            newLength[0] = self.buildConfigFrame(newBuffer, v16);
            pass
            #    CWeatherStationConfig::_CWeatherStationConfig(&result);
        elif (Buffer[0][2] & 0xF) == 3: #(CWeatherStationConfig *)
            loginf('WS SetTime - Request Time Set')
            logdbg("handleNextAction - Set Time Data")
            self.shid.setSleep(0.400,0.005) #95,5
            newLength[0] = self.buildTimeFrame(newBuffer, 1);
        else:
            logdbg("handleNextAction Buffer[2] == %x" % (Buffer[0][2] & 0xF))
            if   rt == ERequestType.rtGetCurrent: #rtGetCurrent
                newLength[0] = self.buildACKFrame(newBuffer, 5, DeviceCS, HistoryIndex, 0xFFFFFFFF);
                self.DataStore.setRequestState( ERequestState.rsRunning);
            elif rt == ERequestType.rtGetHistory: #rtGetHistory
                newLength[0] = self.buildACKFrame(newBuffer, 4, DeviceCS, HistoryIndex, 0xFFFFFFFF);
                self.DataStore.setRequestState( ERequestState.rsRunning);
            elif rt == ERequestType.rtGetConfig: #rtGetConfig
                newLength[0] = self.buildACKFrame(newBuffer, 3, DeviceCS, HistoryIndex, 0xFFFFFFFF);
                self.DataStore.setRequestState( ERequestState.rsRunning);
            elif rt == ERequestType.rtSetConfig: #rtSetConfig
                newLength[0] = self.buildACKFrame(newBuffer, 2, DeviceCS, HistoryIndex, 0xFFFFFFFF);
                self.DataStore.setRequestState( ERequestState.rsRunning);
            elif rt == ERequestType.rtSetTime: #rtSetTime
                newLength[0] = self.buildACKFrame(newBuffer, 1, DeviceCS, HistoryIndex, 0xFFFFFFFF);
                self.DataStore.setRequestState( ERequestState.rsRunning);
            else:
                if ( self.DataStore.getFlag_FLAG_FAST_CURRENT_WEATHER() ):
                    newLength[0] = self.buildACKFrame(newBuffer, 5, DeviceCS, HistoryIndex, 0xFFFFFFFF);
                else:
                    newLength[0] = self.buildACKFrame(newBuffer, 0, DeviceCS, HistoryIndex, 0xFFFFFFFF);
        Length[0] = newLength[0]
        Buffer[0] = newBuffer[0]

    def configureRegisterNames(self):
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.IFMODE]    =0x00
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.MODULATION]=0x41 #fsk
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.ENCODING]  =0x07
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.FRAMING]   =0x84 #1000:0100 ##?hdlc? |1000 010 0
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.CRCINIT3]  =0xff
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.CRCINIT2]  =0xff
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.CRCINIT1]  =0xff
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.CRCINIT0]  =0xff
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.FREQ3]     =0x38
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.FREQ2]     =0x90
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.FREQ1]     =0x00
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.FREQ0]     =0x01
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.PLLLOOP]   =0x1d
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.PLLRANGING]=0x08
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.PLLRNGCLK] =0x03
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.MODMISC]   =0x03
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.SPAREOUT]  =0x00
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.TESTOBS]   =0x00
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.APEOVER]   =0x00
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.TMMUX]     =0x00
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.PLLVCOI]   =0x01
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.PLLCPEN]   =0x01
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.RFMISC]    =0xb0
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.REF]       =0x23
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.IFFREQHI]  =0x20
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.IFFREQLO]  =0x00
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.ADCMISC]   =0x01
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.AGCTARGET] =0x0e
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.AGCATTACK] =0x11
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.AGCDECAY]  =0x0e
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.CICDEC]    =0x3f
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.DATARATEHI]=0x19
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.DATARATELO]=0x66
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.TMGGAINHI] =0x01
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.TMGGAINLO] =0x96
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.PHASEGAIN] =0x03
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.FREQGAIN]  =0x04
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.FREQGAIN2] =0x0a
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.AMPLGAIN]  =0x06
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.AGCMANUAL] =0x00
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.ADCDCLEVEL]=0x10
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.RXMISC]    =0x35
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.FSKDEV2]   =0x00
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.FSKDEV1]   =0x31
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.FSKDEV0]   =0x27
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.TXPWR]     =0x03
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.TXRATEHI]  =0x00
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.TXRATEMID] =0x51
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.TXRATELO]  =0xec
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.TXDRIVER]  =0x88

    def calculateFrequency(self,Frequency):
        logdbg('calculateFrequency')
        FreqVal =  long(Frequency / 16000000.0 * 16777216.0)
        FreqCorrection = [None]
        if self.shid.readConfigFlash(0x1F5, 4, FreqCorrection):
            CorVal = FreqCorrection[0][0] << 8
            CorVal |= FreqCorrection[0][1]
            CorVal <<= 8
            CorVal |= FreqCorrection[0][2]
            CorVal <<= 8
            CorVal |= FreqCorrection[0][3]
            logdbg("CorVal: %x" % CorVal) #0x184e8
            FreqVal += CorVal

    #print "try to tune sensors"
    #Frequency = 915450000
    #FreqVal =  long(Frequency / 16000000.0 * 16777216.0)

        if ( not (FreqVal % 2) ):
            FreqVal += 1
            #FreqVal = 949060841 0x389184e9
            #print "Freq:",CorVal,(CorVal / 16777216 * 16000000 + 1)
            #FreqVal= 915450000 / 16000000 * 16777216 + 1
            #print "experiment:",FreqVal,CorVal
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.FREQ3] = (FreqVal >>24) & 0xFF
        #print "dd %x" % (self.AX5051RegisterNames_map[self.AX5051RegisterNames.FREQ3])
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.FREQ2] = (FreqVal >>16) & 0xFF
        #print "dd %x" % (self.AX5051RegisterNames_map[self.AX5051RegisterNames.FREQ2])
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.FREQ1] = (FreqVal >>8)  & 0xFF
        #print "dd %x" % (self.AX5051RegisterNames_map[self.AX5051RegisterNames.FREQ1])
        self.AX5051RegisterNames_map[self.AX5051RegisterNames.FREQ0] = (FreqVal >>0)  & 0xFF
        #print "dd %x" % (self.AX5051RegisterNames_map[self.AX5051RegisterNames.FREQ0])
        logdbg("FreqVal: %x" % FreqVal)

    def generateResponse(self,Buffer,Length):
        newBuffer = [0]
        newBuffer[0] = Buffer[0]
        newLength = [0]
        newLength[0] = Length[0]
        if Length[0] != 0:
            RequestType = self.DataStore.getRequestType()
            logdbg("generateResponse: Length=%02x RequestType=%x" % (Length[0], RequestType))
            ID = (Buffer[0][0] <<8) | Buffer[0][1]
            if ID == 0xF0F0:
                loginf('generateResponse: FirstTimeConfig, attempting to register. ID=%04X' % ID)
                buf = [None]
                self.shid.readConfigFlash(0x1fe, 2, buf);
                #    00000000: dd 0a 01 fe 18 f6 aa 01 2a a2 4d 00 00 87 16
                DeviceCS = buf[0][1] | (buf[0][0] << 8);
                self.DataStore.WeatherStationConfig.setDeviceCS(DeviceCS)
                self.DataStore.setDeviceRegistered(True)
                HistoryIndex = 0xffff
                newLength[0] = self.buildACKFrame(newBuffer,3,DeviceCS,HistoryIndex,0xFFFFFFFF)  
            else: 
                RegisterdID = self.DataStore.getDeviceId()
                if ID == RegisterdID: 
                    responseType = (Buffer[0][2] & 0xE0)
                    logdbg("Length %02x RegisteredID x%04x responseType: x%02x" % (Length[0], RegisterdID, responseType))
                    if responseType == 0x20:
                        #    00000000: 00 00 06 00 32 20
                        if Length[0] == 0x06:
                            pollcount = self.shid.getPollCount()
                            loginf('WS SetTime / SetConfig - Data written, pollcount=%s' % pollcount)
                            self.DataStore.WeatherStationConfig.setResetMinMaxFlags(0) # zero resetflags
                            self.DataStore.setRequestType(ERequestType.rtGetCurrent)
                            self.handleWsAck(newBuffer, newLength);
                        else:
                            newLength[0] = 0
                    elif responseType == 0x40:
                        #    00000000: 00 00 30 00 32 40
                        if Length[0] == 0x30:
                            self.handleConfig(newBuffer, newLength);
                        else:
                            newLength[0] = 0
                    elif responseType == 0x60:
                        #    00000000: 00 00 d7 00 32 60
                        if Length[0] == 0xd7: #215
                            self.handleCurrentData(newBuffer, newLength);
                        else:
                            newLength[0] = 0
                    elif responseType == 0x80:
                        #    00000000: 00 00 1e 00 32 80
                        if Length[0] == 0x1e:
                            self.handleHistoryData(newBuffer, newLength);
                        else:
                            newLength[0] = 0
                    elif responseType == 0xa0:
                        #    00000000: 00 00 06 f0 f0 a1
                        #    00000000: 00 00 06 00 32 a3
                        #    00000000: 00 00 06 00 32 a2
                        if Length[0] == 0x06:
                            self.handleNextAction(newBuffer, newLength);
                        else:
                            newLength[0] = 0
                    else:
                        logcrt('unrecognized response type %x', responseType)
                        newLength[0] = 0
                else:
                    logcrt("unrecognized transceiver ID %04x, expecting %04x" %
                           (ID, RegisterdID))
                    newLength[0] = 0

        Buffer[0] = newBuffer[0]
        Length[0] = newLength[0]
        if newLength[0] == 0:
            return 0
        return 1

    def transceiverInit(self):
        logdbg('transceiverInit')

        self.configureRegisterNames()
        self.calculateFrequency(self.DataStore.TransceiverSettings.Frequency)

        errmsg = ''
        buf = [None]
        if self.shid.readConfigFlash(0x1F9, 7, buf):
            ID  = buf[0][5] << 8
            ID += buf[0][6]
            logerr("DeviceID=0x%x" % ID)
            self.DataStore.setDeviceId(ID)

            SN  = str("%02d"%(buf[0][0]))
            SN += str("%02d"%(buf[0][1]))
            SN += str("%02d"%(buf[0][2]))
            SN += str("%02d"%(buf[0][3]))
            SN += str("%02d"%(buf[0][4]))
            SN += str("%02d"%(buf[0][5]))
            SN += str("%02d"%(buf[0][6]))
            self.DataStore.setTransceiverSerNo(SN)
            
            for Register in self.AX5051RegisterNames_map:
                self.shid.writeReg(Register,self.AX5051RegisterNames_map[Register])

            if self.shid.execute(5):
                loginf('setPreamblePattern(0xaa)')
                self.shid.setPreamblePattern(0xaa)
                loginf('setState(0x1e); push SET button if stopped')
                if self.shid.setState(0x1e):
                    time.sleep(1)
                    if self.shid.setRX():
                        pass
                    else:
                        loginf('shid.setRX failed')
                else:
                    loginf('shid.setState failed')
            else:
                errmsg = 'shid.execute failed'
        else:
            errmsg = 'shid.readConfig failed'

        if errmsg != '':
            raise Exception('transceiver initialization failed: %s' % errmsg)

    def startRFThread(self):
        logdbg('startRFThread')
        child = threading.Thread(target=self.doRF)
        child.setName('WS28xx_RF_Communication')
        child.start()

    def stopRFThread(self):
        logdbg('stopRFThread')
        self.running = False

    def doRF(self):
        try:
            ComInt = self.DataStore.getCommModeInterval();
            loginf('Initializing rf communication, WeatherData Interval=%i s' % (ComInt+1))
            self.DataStore.setFlag_FLAG_TRANSCEIVER_SETTING_CHANGE(1)
            self.shid.open()
            self.transceiverInit()
            self.DataStore.setFlag_FLAG_TRANSCEIVER_PRESENT( 1)
            self.shid.setRX()
            while self.running:
                self.doRFCommunication()
        except Exception, e:
            logerr('exception in doRF: %s' % e)
            traceback.print_exc()
            self.running = False
            raise

    def doRFCommunication(self):
        DataLength = [0]
        DataLength[0] = 0
        StateBuffer = [None]
        if self.shid.getState(StateBuffer) != 1:
            logerr('getState failed')
        else:
            FrameBuffer=[0]
            FrameBuffer[0]=[0]*0x03
            ret = self.shid.getFrame(FrameBuffer, DataLength)
            if ret == 0:
                raise Exception("getFrame failed")
            if ret == 2: #2= double message
                logerr('getFrame double message')
            else:
                if self.generateResponse(FrameBuffer, DataLength) == 1:
                    self.shid.setState(0)
                    if self.shid.setFrame(FrameBuffer[0], DataLength[0]) != 1: # send the ackframe prepared by generateResponse
                        logerr('setFrame failed')
            if self.shid.setTX() != 1:
                logerr("setTX failed")