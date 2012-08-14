#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright (C) 2011-2012 WikiTeam
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

# Script to check if a list of wikis are alive or dead

import thread
import time
import sys
import urllib2

#configuration
delay = 10
limit = 100

def checkcore(api):
    try:
        if '&lt;api' in urllib2.urlopen(api, None, delay).read():
            open('wikisalive.txt', 'a').write(('%s\n' % api.strip()).encode('utf-8'))
    except:
        pass

def check(apis):
    for api in apis:
        thread.start_new_thread(checkcore, (api,))
        time.sleep(0.1)
    time.sleep(delay+1)

apis = []
for api in open('wikistocheck.txt', 'r').read().strip().splitlines():
    if not api in apis:
        apis.append(api)
    if len(apis) >= limit:
        check(apis)
        apis = []

check(apis)
