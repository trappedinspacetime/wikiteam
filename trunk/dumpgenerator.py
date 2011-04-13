# -*- coding: utf-8 -*-

# Copyright (C) 2011 WikiTeam
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

import cPickle
import datetime
import getopt
import md5
import os
import re
import subprocess
import sys
import time
import urllib
import urllib2

# todo:
# curonly and all history (curonly si puede acumular varias peticiones en un solo GET, ara full history pedir cada pagina una a una)
# usar api o parsear html si no está disponible
# http://www.mediawiki.org/wiki/Manual:Parameters_to_Special:Export
# threads para bajar más rápido? pedir varias páginas a la vez
# Special:Log? uploads, account creations, etc
# download Special:Version to save whch extension it used
# que guarde el index.php (la portada) como index.html para que se vea la licencia del wiki abajo del todo
# fix use api when available

def truncateFilename(other={}, filename=''):
    return filename[:other['filenamelimit']] + md5.new(filename).hexdigest() + '.' + filename.split('.')[-1]

def delay(config={}):
    if config['delay'] > 0:
        print 'Sleeping... %d seconds...' % (config['delay'])
        time.sleep(config['delay'])

def cleanHTML(raw=''):
    #<!-- bodytext --> <!-- /bodytext -->
    #<!-- start content --> <!-- end content -->
    #<!-- Begin Content Area --> <!-- End Content Area -->
    if re.search('<!-- bodytext -->', raw):
        raw = raw.split('<!-- bodytext -->')[1].split('<!-- /bodytext -->')[0]
    elif re.search('<!-- start content -->', raw):
        raw = raw.split('<!-- start content -->')[1].split('<!-- end content -->')[0]
    elif re.search('<!-- Begin Content Area -->', raw):
        raw = raw.split('<!-- Begin Content Area -->')[1].split('<!-- End Content Area -->')[0]
    else:
        print 'This wiki doesn\'t use marks to split contain'
        sys.exit()
    return raw

def getNamespaces(config={}):
    #namespace checks and stuff
    #fix get namespaces from a random Special:Export page, it is better
    #too from API http://wikiindex.org/api.php?action=query&meta=siteinfo&siprop=general|namespaces
    namespaces = config['namespaces']
    namespacenames = {0:''} # main is 0, no prefix
    if namespaces:
        raw = urllib.urlopen('%s?title=Special:Allpages' % (config['index'])).read()
        m = re.compile(r'<option [^>]*?value="(?P<namespaceid>\d+)"[^>]*?>(?P<namespacename>[^<]+)</option>').finditer(raw) # [^>]*? to include selected="selected"
        if 'all' in namespaces:
            namespaces = []
            for i in m:
                namespaces.append(int(i.group("namespaceid")))
                namespacenames[int(i.group("namespaceid"))] = i.group("namespacename")
        else:
            #check if those namespaces really exist in this wiki
            namespaces2 = []
            for i in m:
                if int(i.group("namespaceid")) in namespaces:
                    namespaces2.append(int(i.group("namespaceid")))
                    namespacenames[int(i.group("namespaceid"))] = i.group("namespacename")
            namespaces = namespaces2
    else:
        namespaces = [0]
    
    #retrieve all titles from Special:Allpages, if the wiki is big, perhaps there are sub-Allpages to explore
    namespaces = [i for i in set(namespaces)] #uniques
    print '%d namespaces have been found' % (len(namespaces))
    return namespaces, namespacenames

def getPageTitlesAPI(config={}):
    titles = []
    namespaces, namespacenames = getNamespaces(config=config)
    for namespace in namespaces:
        if namespace in config['exnamespaces']:
            print '    Skiping namespace =', namespace
            continue
        
        c = 0
        print '    Retrieving titles in the namespace', namespace
        headers = {'User-Agent': getUserAgent()}
        apfrom = '!'
        while apfrom:
            params = {'action': 'query', 'list': 'allpages', 'apnamespace': namespace, 'apfrom': apfrom, 'format': 'xml', 'aplimit': 500}
            data = urllib.urlencode(params)
            req = urllib2.Request(url=config['api'], data=data, headers=headers)
            try:
                f = urllib2.urlopen(req)
            except:
                try:
                    print 'Server is slow... Waiting some seconds and retrying...'
                    time.sleep(10)
                    f = urllib2.urlopen(req)
                except:
                    print 'An error have occurred while retrieving page titles with API'
                    print 'Please, resume the dump, --resume'
                    sys.exit()
            xml = f.read()
            f.close()
            m = re.findall(r'<allpages apfrom="([^>]+)" />', xml)
            if m:
                apfrom = undoHTMLEntities(text=m[0]) #&quot; = ", etc
            else:
                apfrom = ''
            m = re.findall(r'title="([^>]+)" />', xml)
            titles += [undoHTMLEntities(title) for title in m]
            c += len(m)
        print '    %d titles retrieved in the namespace %d' % (c, namespace)
    return titles

def getPageTitlesScrapper(config={}):
    titles = []
    namespaces, namespacenames = getNamespaces(config=config)
    for namespace in namespaces:
        print '    Retrieving titles in the namespace', namespace
        url = '%s?title=Special:Allpages&namespace=%s' % (config['index'], namespace)
        raw = urllib.urlopen(url).read()
        raw = cleanHTML(raw)
        
        r_title = r'title="(?P<title>[^>]+)">'
        r_suballpages = ''
        r_suballpages1 = r'&amp;from=(?P<from>[^>]+)&amp;to=(?P<to>[^>]+)">'
        r_suballpages2 = r'Special:Allpages/(?P<from>[^>]+)">'
        if re.search(r_suballpages1, raw):
            r_suballpages = r_suballpages1
        elif re.search(r_suballpages2, raw):
            r_suballpages = r_suballpages2
        else:
            pass #perhaps no subpages
        
        deep = 3 # 3 is the current deep of English Wikipedia for Special:Allpages, 3 levels
        c = 0
        checked_suballpages = []
        rawacum = raw
        while r_suballpages and re.search(r_suballpages, raw) and c < deep:
            #load sub-Allpages
            m = re.compile(r_suballpages).finditer(raw)
            for i in m:
                fr = i.group('from')
                
                if r_suballpages == r_suballpages1:
                    to = i.group('to')
                    name = '%s-%s' % (fr, to)
                    url = '%s?title=Special:Allpages&namespace=%s&from=%s&to=%s' % (config['index'], namespace, fr, to) #do not put urllib.quote in fr or to
                elif r_suballpages == r_suballpages2: #fix, esta regexp no carga bien todas? o falla el r_title en este tipo de subpag? (wikiindex)
                    fr = fr.split('&amp;namespace=')[0] #clean &amp;namespace=\d, sometimes happens
                    name = fr
                    url = '%s?title=Special:Allpages/%s&namespace=%s' % (config['index'], name, namespace)
                
                if not name in checked_suballpages:
                    checked_suballpages.append(name) #to avoid reload dupe subpages links
                    raw2 = urllib.urlopen(url).read()
                    raw2 = cleanHTML(raw2)
                    rawacum += raw2 #merge it after removed junk
                    print '    Reading', name, len(raw2), 'bytes', len(re.findall(r_suballpages, raw2)), 'subpages', len(re.findall(r_title, raw2)), 'pages'
            c += 1
        
        c = 0
        m = re.compile(r_title).finditer(rawacum)
        for i in m:
            if not i.group('title').startswith('Special:'):
                if not i.group('title') in titles:
                    titles.append(i.group('title'))
                    c += 1
        print '    %d titles retrieved in the namespace %d' % (c, namespace)
    return titles

def getPageTitles(config={}):
    #Get page titles parsing Special:Allpages or using API (fix)
    #http://en.wikipedia.org/wiki/Special:AllPages
    #http://archiveteam.org/index.php?title=Special:AllPages
    #http://www.wikanda.es/wiki/Especial:Todas
    print 'Loading page titles from namespaces = %s' % (config['namespaces'] and ','.join([str(i) for i in config['namespaces']]) or 'None')
    print 'Excluding titles from namespaces = %s' % (config['exnamespaces'] and ','.join([str(i) for i in config['exnamespaces']]) or 'None')
    
    titles = []
    if config['api']:
        titles = getPageTitlesAPI(config=config)
    elif config['index']:
        titles = getPageTitlesScrapper(config=config)
    
    print '%d page titles loaded' % (len(titles))
    return titles

def getXMLHeader(config={}):
    #get the header of a random page, to attach it in the complete XML backup
    #similar to: <mediawiki xmlns="http://www.mediawiki.org/xml/export-0.3/" xmlns:x....
    randomtitle = 'AMF5LKE43MNFGHKSDMRTJ'
    xml = getXMLPage(config=config, title=randomtitle)
    header = xml.split('</mediawiki>')[0]
    return header

def getXMLFileDesc(config={}, title=''):
    config['curonly'] = 1 #tricky to get only the most recent desc
    return getXMLPage(config=config, title=title)

def getUserAgent():
    useragents = ['Mozilla/5.0 (Windows; U; Windows NT 5.1; en-GB; rv:1.8.0.4) Gecko/20060508 Firefox/1.5.0.4']
    return useragents[0]

def getXMLPage(config={}, title=''):
    #http://www.mediawiki.org/wiki/Manual_talk:Parameters_to_Special:Export#Parameters_no_longer_in_use.3F
    limit = 1000
    truncated = False
    title_ = title
    title_ = re.sub(' ', '_', title_)
    title_ = re.sub('&', '%26', title_) # titles with & need to be converted into %26
    headers = {'User-Agent': getUserAgent()}
    params = {'title': 'Special:Export', 'pages': title_, 'action': 'submit', }
    if config['curonly']:
        params['curonly'] = 1
    else:
        params['offset'] = '1'
        params['limit'] = limit
    data = urllib.urlencode(params)
    req = urllib2.Request(url=config['index'], data=data, headers=headers)
    try:
        f = urllib2.urlopen(req)
    except:
        try:
            print 'Server is slow... Waiting some seconds and retrying...'
            time.sleep(10)
            f = urllib2.urlopen(req)
        except:
            print 'An error have occurred while retrieving "%s"' % (title)
            print 'Please, resume the dump, --resume'
            sys.exit()
    xml = f.read()

    #if complete history, check if this page history has > limit edits, if so, retrieve all using offset if available
    #else, warning about Special:Export truncating large page histories
    r_timestamp = r'<timestamp>([^<]+)</timestamp>'
    if not config['curonly'] and re.search(r_timestamp, xml): # search for timestamps in xml to avoid analysing empty pages like Special:Allpages and the random one
        while not truncated and params['offset']:
            params['offset'] = re.findall(r_timestamp, xml)[-1] #get the last timestamp from the acum XML
            data = urllib.urlencode(params)
            req2 = urllib2.Request(url=config['index'], data=data, headers=headers)
            try:
                f2 = urllib2.urlopen(req2)
            except:
                try:
                    print 'Sever is slow... Waiting some seconds and retrying...'
                    time.sleep(10)
                    f2 = urllib2.urlopen(req2)
                except:
                    print 'An error have occurred while retrieving', title
                    print 'Please, resume the dump, --resume'
                    sys.exit()
            xml2 = f2.read()
            if re.findall(r_timestamp, xml2): #are there more edits in this next XML chunk?
                if re.findall(r_timestamp, xml2)[-1] == params['offset']:
                    #again the same XML, this wiki does not support params in Special:Export, offer complete XML up to X edits (usually 1000)
                    print 'ATTENTION: This wiki does not allow some parameters in Special:Export, so, pages with large histories may be truncated'
                    truncated = True
                    break
                else:
                    #offset is OK in this wiki, merge with the previous chunk of this page history and continue
                    xml = xml.split('</page>')[0]+xml2.split('<page>\n')[1]
            else:
                params['offset'] = '' #no more edits in this page history
            print title, len(re.findall(r_timestamp, xml)), 'edits'
    return xml

def cleanXML(xml=''):
    #do not touch xml codification, as is
    xml = xml.split('</siteinfo>\n')[1]
    xml = xml.split('</mediawiki>')[0]
    return xml

def generateXMLDump(config={}, titles=[], start=''):
    print 'Retrieving the XML for every page from "%s"' % (start and start or 'start')
    header = getXMLHeader(config=config)
    footer = '</mediawiki>\n' #new line at the end
    xmlfilename = '%s-%s-%s.xml' % (domain2prefix(config=config), config['date'], config['curonly'] and 'current' or 'history')
    xmlfile = ''
    lock = True
    if start:
        #remove the last chunk of xml dump (it is probably incomplete)
        xmlfile = open('%s/%s' % (config['path'], xmlfilename), 'r')
        xml = xmlfile.read()
        xmlfile.close()
        xml = xml.split('<title>%s</title>' % (start))[0]
        xml = '\n'.join(xml.split('\n')[:-2]) # [:-1] removing <page>\n tag
        xmlfile = open('%s/%s' % (config['path'], xmlfilename), 'w')
        xmlfile.write('%s\n' % (xml))
        xmlfile.close()
    else:
        #requested complete xml dump
        lock = False
        xmlfile = open('%s/%s' % (config['path'], xmlfilename), 'w')
        xmlfile.write(header)
        xmlfile.close()
    
    xmlfile = open('%s/%s' % (config['path'], xmlfilename), 'a')
    c = 1
    for title in titles:
        if title == start: #start downloading from start, included
            lock = False
        if lock:
            continue
        delay(config=config)
        if c % 10 == 0:
            print '    Downloaded %d pages' % (c)
        xml = getXMLPage(config=config, title=title)
        xml = cleanXML(xml=xml)
        xmlfile.write(xml)
        c += 1
    xmlfile.write(footer)
    xmlfile.close()
    print 'XML dump saved at...', xmlfilename

def saveTitles(config={}, titles=[]):
    #save titles in a txt for resume if needed
    titlesfilename = '%s-%s-titles.txt' % (domain2prefix(config=config), config['date'])
    titlesfile = open('%s/%s' % (config['path'], titlesfilename), 'w')
    titlesfile.write('\n'.join(titles))
    titlesfile.write('\n--END--')
    titlesfile.close()
    print 'Titles saved at...', titlesfilename

def saveImageFilenamesURL(config={}, images=[]):
    #save list of images and their urls
    imagesfilename = '%s-%s-images.txt' % (domain2prefix(config=config), config['date'])
    imagesfile = open('%s/%s' % (config['path'], imagesfilename), 'w')
    imagesfile.write('\n'.join(['%s\t%s\t%s' % (filename, url, uploader) for filename, url, uploader in images]))
    imagesfile.write('\n--END--')
    imagesfile.close()
    print 'Image filenames and URLs saved at...', imagesfilename

def getImageFilenamesURL(config={}):
    #fix start is only available if parsing from API, if not, reload all the list from special:imagelist is mandatory
    print 'Retrieving image filenames'
    r_next = r'(?<!&amp;dir=prev)&amp;offset=(?P<offset>\d+)&amp;' # (?<! http://docs.python.org/library/re.html
    images = []
    offset = '29990101000000' #january 1, 2999
    while offset:
        url = '%s?title=Special:Imagelist&limit=5000&offset=%s' % (config['index'], offset)
        raw = urllib.urlopen(url).read()
        raw = cleanHTML(raw)
        #archiveteam 1.15.1 <td class="TablePager_col_img_name"><a href="/index.php?title=File:Yahoovideo.jpg" title="File:Yahoovideo.jpg">Yahoovideo.jpg</a> (<a href="/images/2/2b/Yahoovideo.jpg">file</a>)</td>
        #wikanda 1.15.5 <td class="TablePager_col_img_user_text"><a href="/w/index.php?title=Usuario:Fernandocg&amp;action=edit&amp;redlink=1" class="new" title="Usuario:Fernandocg (página no existe)">Fernandocg</a></td>
        r_images1 = r'(?im)<td class="TablePager_col_img_name"><a href[^>]+title="[^:>]+:(?P<filename>[^>]+)">[^<]+</a>[^<]+<a href="(?P<url>[^>]+/[^>/]+)">[^<]+</a>[^<]+</td>\s*<td class="TablePager_col_img_user_text"><a[^>]+>(?P<uploader>[^<]+)</a></td>'
        #wikijuegos 1.9.5 http://softwarelibre.uca.es/wikijuegos/Especial:Imagelist old mediawiki version
        r_images2 = r'(?im)<td class="TablePager_col_links"><a href[^>]+title="[^:>]+:(?P<filename>[^>]+)">[^<]+</a>[^<]+<a href="(?P<url>[^>]+/[^>/]+)">[^<]+</a></td>\s*<td class="TablePager_col_img_timestamp">[^<]+</td>\s*<td class="TablePager_col_img_name">[^<]+</td>\s*<td class="TablePager_col_img_user_text"><a[^>]+>(?P<uploader>[^<]+)</a></td>'
        #gentoowiki 1.18 <tr><td class="TablePager_col_img_timestamp">18:15, 3 April 2011</td><td class="TablePager_col_img_name"><a href="/wiki/File:Asus_eeepc-1201nl.png" title="File:Asus eeepc-1201nl.png">Asus eeepc-1201nl.png</a> (<a href="/w/images/2/2b/Asus_eeepc-1201nl.png">file</a>)</td><td class="TablePager_col_thumb"><a href="/wiki/File:Asus_eeepc-1201nl.png" class="image"><img alt="" src="/w/images/thumb/2/2b/Asus_eeepc-1201nl.png/180px-Asus_eeepc-1201nl.png" width="180" height="225" /></a></td><td class="TablePager_col_img_size">37 KB</td><td class="TablePager_col_img_user_text"><a href="/w/index.php?title=User:Yannails&amp;action=edit&amp;redlink=1" class="new" title="User:Yannails (page does not exist)">Yannails</a></td><td class="TablePager_col_img_description">&#160;</td><td class="TablePager_col_count">1</td></tr>
        r_images3 = r'(?im)<td class="TablePager_col_img_name"><a[^>]+title="[^:>]+:(?P<filename>[^>]+)">[^<]+</a>[^<]+<a href="(?P<url>[^>]+)">[^<]+</a>[^<]+</td><td class="TablePager_col_thumb"><a[^>]+><img[^>]+></a></td><td class="TablePager_col_img_size">[^<]+</td><td class="TablePager_col_img_user_text"><a[^>]+>(?P<uploader>[^<]+)</a></td>'
        m = []
        #different mediawiki versions
        if re.search(r_images1, raw):
            m = re.compile(r_images1).finditer(raw)
        elif re.search(r_images2, raw):
            m = re.compile(r_images2).finditer(raw)
        elif re.search(r_images3, raw):
            m = re.compile(r_images3).finditer(raw)
        
        for i in m:
            url = i.group('url')
            if url[0] == '/' or not url.startswith('http://'): #relative URL
                if url[0] == '/': #it is added later
                    url = url[1:]
                domainalone = config['index'].split('http://')[1].split('/')[0]
                url = 'http://%s/%s' % (domainalone, url)
            url = undoHTMLEntities(text=url)
            #url = urllib.unquote(url) #do not use unquote with url, it break some urls with odd chars
            url = re.sub(' ', '_', url)
            filename = re.sub('_', ' ', i.group('filename'))
            filename = undoHTMLEntities(text=filename)
            filename = urllib.unquote(filename)
            uploader = re.sub('_', ' ', i.group('uploader'))
            uploader = undoHTMLEntities(text=uploader)
            uploader = urllib.unquote(uploader)
            images.append([filename, url, uploader])
            #print filename, url
        
        if re.search(r_next, raw):
            offset = re.findall(r_next, raw)[0]
        else:
            offset = ''
    
    print '    Found %d images' % (len(images))
    images.sort()
    return images

def undoHTMLEntities(text=''):
    text = re.sub('&lt;', '<', text) # i guess only < > & " need conversion http://www.w3schools.com/html/html_entities.asp
    text = re.sub('&gt;', '>', text)
    text = re.sub('&amp;', '&', text)
    text = re.sub('&quot;', '"', text)
    return text

def generateImageDump(config={}, other={}, images=[], start=''):
    #slurp all the images
    #save in a .tar?
    #tener en cuenta http://www.mediawiki.org/wiki/Manual:ImportImages.php
    #fix, download .desc ? YEP!
    #fix download the upload log too, for uploaders info and date
    print 'Retrieving images from "%s"' % (start and start or 'start')
    imagepath = '%s/images' % (config['path'])
    if os.path.isdir(imagepath):
        print 'It exists an images directory for this dump' #fix, resume?
    else:
        os.makedirs(imagepath)
    
    c = 0
    lock = True
    if not start:
        lock = False
    for filename, url, uploader in images:
        if filename == start: #start downloading from start, included
            lock = False
        if lock:
            continue
        delay(config=config)
        #saving file
        #truncate filename if length > 100 (100 + 32 (md5) = 132 < 143 (crash limit). Later .desc is added to filename, so better 100 as max)
        filename2 = filename
        if len(filename2) > other['filenamelimit']:
            # split last . (extension) and then merge
            filename2 = truncateFilename(other=other, filename=filename2)
            print 'Truncating filename, it is too long. Now it is called:', filename2
        urllib.urlretrieve(url, '%s/%s' % (imagepath, filename2)) 
        #saving description if any
        xmlfiledesc = getXMLFileDesc(config=config, title='Image:%s' % (filename)) 
        f = open('%s/%s.desc' % (imagepath, filename2), 'w')
        if re.search(r'<text xml:space="preserve"/>', xmlfiledesc):
            #empty desc
            xmlfiledesc = ''
        elif re.search(r'<text xml:space="preserve">', xmlfiledesc):
            xmlfiledesc = xmlfiledesc.split('<text xml:space="preserve">')[1].split('</text>')[0]
            xmlfiledesc = undoHTMLEntities(text=xmlfiledesc)
        else: #failure when retrieving desc?
            xmlfiledesc = ''
        f.write(xmlfiledesc)
        f.close()
        c += 1
        if c % 10 == 0:
            print '    Downloaded %d images' % (c)
    print 'Downloaded %d images' % (c)
    
def saveLogs(config={}):
    #get all logs from Special:Log
    """parse
    <select name='type'>
    <option value="block">Bloqueos de usuarios</option>
    <option value="rights">Cambios de perfil de usuario</option>
    <option value="protect" selected="selected">Protecciones de páginas</option>
    <option value="delete">Registro de borrados</option>
    <option value="newusers">Registro de creación de usuarios</option>
    <option value="merge">Registro de fusiones</option>
    <option value="import">Registro de importaciones</option>
    <option value="patrol">Registro de revisiones</option>
    <option value="move">Registro de traslados</option>
    <option value="upload">Subidas de archivos</option>
    <option value="">Todos los registros</option>
    </select>
    """
    delay(config=config)

def domain2prefix(config={}):
    domain = ''
    if config['api']:
        domain = config['api']
    elif config['index']:
        domain = config['index']
    domain = domain.lower()
    domain = re.sub(r'(http://|www\.|/index\.php|/api\.php)', '', domain)
    domain = re.sub(r'/', '_', domain)
    domain = re.sub(r'\.', '', domain)
    domain = re.sub(r'[^A-Za-z0-9]', '_', domain)
    return domain

def loadConfig(config={}, configfilename=''):
    f = open('%s/%s' % (config['path'], configfilename), 'r')
    config = cPickle.load(f)
    f.close()
    return config

def saveConfig(config={}, configfilename=''):
    f = open('%s/%s' % (config['path'], configfilename), 'w')
    cPickle.dump(config, f)
    f.close()
    
def welcome():
    print "#"*73
    print """# Welcome to DumpGenerator 0.1 by WikiTeam (GPL v3)                     #
# More info at: http://code.google.com/p/wikiteam/                      #"""
    print "#"*73
    print ''
    print "#"*73
    print """# Copyright (C) 2011 WikiTeam                                           #
# This program is free software: you can redistribute it and/or modify  #
# it under the terms of the GNU General Public License as published by  #
# the Free Software Foundation, either version 3 of the License, or     #
# (at your option) any later version.                                   #
#                                                                       #
# This program is distributed in the hope that it will be useful,       #
# but WITHOUT ANY WARRANTY; without even the implied warranty of        #
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         #
# GNU General Public License for more details.                          #
#                                                                       #
# You should have received a copy of the GNU General Public License     #
# along with this program.  If not, see <http://www.gnu.org/licenses/>. #"""
    print "#"*73
    print ''

def bye():
    print "Your dump is complete"
    print "If you found any bug, report a new issue here (Gmail account required): http://code.google.com/p/wikiteam/issues/list"
    print "Good luck! Bye!"

def usage():
    print "Write a complete help"

def getParameters():
    config = {
        'curonly': False,
        'date': datetime.datetime.now().strftime('%Y%m%d'),
        'api': '',
        'index': '',
        'images': False,
        'logs': False,
        'xml': False,
        'namespaces': ['all'],
        'exnamespaces': [],
        'path': '',
        'threads': 1, #fix not coded yet
        'delay': 0,
    }
    other = {
        'resume': False,
        'filenamelimit': 100, #do not change
    }
    #console params
    try:
        opts, args = getopt.getopt(sys.argv[1:], "", ["h", "help", "path=", "api=", "index=", "images", "logs", "xml", "curonly", "threads=", "resume", "delay=", "namespaces=", "exnamespaces=", ])
    except getopt.GetoptError, err:
        # print help information and exit:
        print str(err) # will print something like "option -a not recognized"
        usage()
        sys.exit(2)
    for o, a in opts:
        if o in ("-h","--help"):
            usage()
        elif o in ("--path"):
            config["path"] = a
            while len(config["path"])>0:
                if config["path"][-1] == '/': #dará problemas con rutas windows?
                    config["path"] = config["path"][:-1]
                else:
                    break
        elif o in ("--api"):
            config['api'] = a
        elif o in ("--index"):
            config["index"] = a
        elif o in ("--images"):
            config["images"] = True
        elif o in ("--logs"):
            config["logs"] = True
        elif o in ("--xml"):
            config["xml"] = True
        elif o in ("--curonly"):
            if not config["xml"]:
                print "If you select --curonly, you must use --xml too"
                sys.exit()
            config["curonly"] = True
        elif o in ("--threads"):
            config["threads"] = int(a)
        elif o in ("--resume"):
            other["resume"] = True
        elif o in ("--delay"):
            config["delay"] = int(a)
        elif o in ("--namespaces"):
            if re.search(r'[^\d, \-]', a) and a.lower() != 'all':
                print "Invalid namespaces values.\nValid format is integer(s) splitted by commas"
                sys.exit()
            a = re.sub(' ', '', a)
            if a.lower() == 'all':
                config["namespaces"] = 'all'
            else:
                config["namespaces"] = [int(i) for i in a.split(',')]
        elif o in ("--exnamespaces"):
            if re.search(r'[^\d, \-]', a):
                print "Invalid exnamespaces values.\nValid format is integer(s) splitted by commas"
                sys.exit()
            a = re.sub(' ', '', a)
            if a.lower() == 'all':
                print 'You have excluded all namespaces. Error.'
                sys.exit()
            else:
                config["exnamespaces"] = [int(i) for i in a.split(',')]
        else:
            assert False, "unhandled option"

    #missing mandatory params
    #(config['index'] and not re.search('/index\.php', config['index'])) or \ # in EditThis there is no index.php, it is empty editthis.info/mywiki/?title=...
    if (not config['api'] and not config['index']) or \
       (config['api'] and not re.search('/api\.php', config['api'])) or \
       not (config["xml"] or config["images"] or config["logs"]) or \
       (other['resume'] and not config['path']):
        print """Error. You forget mandatory parameters:
    --api or --index: URL to api.php or to index.php, one of them. If wiki has api.php, please, use --api instead of --index. Examples: --api=http://archiveteam.org/api.php or --index=http://archiveteam.org/index.php
    
And one of these, or two or three:
    --xml: it generates a XML dump. It retrieves full history of pages located in namespace = 0 (articles)
           If you want more namespaces, use the parameter --namespaces=0,1,2,3... or --namespaces=all
    --images: it generates an image dump
    --logs: it generates a log dump

You can resume previous incomplete dumps:
    --resume: it resumes previous incomplete dump. When using --resume, --path is mandatory (path to directory where incomplete dump is).

You can exclude namespaces:
    --exnamespaces: write the number of the namespaces you want to exclude, splitted by commas.

Write --help for help."""
        sys.exit()
        #usage()
    
    #user chosen --api, --index it is neccesary for special:export, we generate it
    if config['api'] and not config['index']:
        config['index'] = config['api'].split('api.php')[0] + 'index.php'
        #print 'You didn\'t provide a path for index.php, trying to wonder one:', config['index']
    
    if config['api']:
        #fix add here api.php existence comprobation
        f = urllib.urlopen(config['api'])
        raw = f.read()
        f.close()
        print 'Checking api.php...'
        if re.search(r'action=query', raw):
            print 'api.php is OK'
        else:
            print 'Error in api.php, please, provide a correct path to api.php'
            sys.exit()
    
    if config['index']:
        #check index.php
        f = urllib.urlopen('%s?title=Special:Version' % (config['index']))
        raw = f.read()
        f.close()
        print 'Checking index.php...'
        if re.search(r'This wiki is powered by', raw):
            print 'index.php is OK'
        else:
            print 'Error in index.php, please, provide a correct path to index.php'
            sys.exit()
    
    #adding http://
    if not config['index'] and not config['api'].startswith('http://'):
        config['api'] = 'http://' + config['api']
    if not config['api'] and not config['index'].startswith('http://'):
        config['index'] = 'http://' + config['index']
    
    
    
    #calculating path, if not defined by user with --path=
    if not config['path']:
        config['path'] = './%s-%s-wikidump' % (domain2prefix(config=config), config['date'])
    
    return config, other

def removeIP(raw=''):
    raw = re.sub(r'\d+\.\d+\.\d+\.\d+', '0.0.0.0', raw)
    #http://www.juniper.net/techpubs/software/erx/erx50x/swconfig-routing-vol1/html/ipv6-config5.html
    #weird cases as :: are not included
    raw = re.sub(r'(?i)[\da-f]{0,4}:[\da-f]{0,4}:[\da-f]{0,4}:[\da-f]{0,4}:[\da-f]{0,4}:[\da-f]{0,4}:[\da-f]{0,4}:[\da-f]{0,4}', '0:0:0:0:0:0:0:0', raw)
    return raw

def main():
    welcome()
    configfilename = 'config.txt'
    config, other = getParameters()
    
    #notice about wikipedia dumps
    if re.findall(r'(wikipedia|wikisource|wiktionary|wikibooks|wikiversity|wikimedia|wikispecies|wikiquote|wikinews)\.org', config['api']+config['index']):
        print 'DO NOT USE THIS SCRIPT TO DOWNLOAD WIKIMEDIA PROJECTS!\nDownload the dumps from http://download.wikimedia.org\nThanks!'
        sys.exit()
    
    print 'Analysing %s' % (config['api'] and config['api'] or config['index'])
    
    #creating path or resuming if desired
    c = 2
    originalpath = config['path'] # to avoid concat blabla-2, blabla-2-3, and so on...
    while not other['resume'] and os.path.isdir(config['path']): #do not enter if resume is request from begining
        print '\nWarning!: "%s" path exists' % (config['path'])
        reply = ''
        while reply not in ['yes', 'y', 'no', 'n']:
            reply = raw_input('There is a dump in "%s", probably incomplete.\nIf you choose resume, to avoid conflicts, the parameters you have chosen in the current session will be ignored\nand the parameters available in "%s/%s" will be loaded.\nDo you want to resume ([yes, y], [no, n])? ' % (config['path'], config['path'], configfilename))
        if reply.lower() in ['yes', 'y']:
            if not os.path.isfile('%s/%s' % (config['path'], configfilename)):
                print 'No config file found. I can\'t resume. Aborting.'
                sys.exit()
            print 'You have selected YES'
            other['resume'] = True
            break
        elif reply.lower() in ['no', 'n']:
            print 'You have selected NO'
            other['resume'] = False
        config['path'] = '%s-%d' % (originalpath, c)
        print 'Trying "%s"...' % (config['path'])
        c += 1

    if other['resume']:
        print 'Loading config file...'
        config = loadConfig(config=config, configfilename=configfilename)
    else:
        os.mkdir(config['path'])
        saveConfig(config=config, configfilename=configfilename)
    
    titles = []
    images = []
    if other['resume']:
        print 'Resuming previous dump process...'
        if config['xml']:
            #load titles
            lasttitle = ''
            try:
                f = open('%s/%s-%s-titles.txt' % (config['path'], domain2prefix(config=config), config['date']), 'r')
                raw = f.read()
                titles = raw.split('\n')
                lasttitle = titles[-1]
                if not lasttitle: #empty line at EOF ?
                    lasttitle = titles[-2]
                f.close()
            except:
                pass #probably file doesnot exists
            if lasttitle == '--END--':
                #titles list is complete
                print 'Title list was completed in the previous session'
            else:
                print 'Title list is incomplete. Reloading...'
                #do not resume, reload, to avoid inconsistences, deleted pages or so
                titles = getPageTitles(config=config)
                saveTitles(config=config, titles=titles)
            #checking xml dump
            xml = ''
            try:
                f = open('%s/%s-%s-%s.xml' % (config['path'], domain2prefix(config=config), config['date'], config['curonly'] and 'current' or 'history'), 'r')
                xml = f.read()
                f.close()
            except:
                pass #probably file doesnot exists
            if re.findall('</mediawiki>', xml):
                #xml dump is complete
                print 'XML dump was completed in the previous session'
            else:
                xmltitles = re.findall(r'<title>([^<]+)</title>', xml)
                lastxmltitle = ''
                if xmltitles:
                    lastxmltitle = xmltitles[-1]
                generateXMLDump(config=config, titles=titles, start=lastxmltitle)
        
        if config['images']:
            #load images
            lastimage = ''
            try:
                f = open('%s/%s-%s-images.txt' % (config['path'], domain2prefix(config=config), config['date']), 'r')
                raw = f.read()
                lines = raw.split('\n')
                for l in lines:
                    if re.search(r'\t', l):
                        images.append(l.split('\t'))
                lastimage = lines[-1]
                f.close()
            except:
                pass #probably file doesnot exists
            if lastimage == '--END--':
                print 'Image list was completed in the previous session'
            else:
                print 'Image list is incomplete. Reloading...'
                #do not resume, reload, to avoid inconsistences, deleted images or so
                images = getImageFilenamesURL(config=config)
                saveImageFilenamesURL(config=config, images=images)
            #checking images directory
            listdir = []
            try:
                listdir = os.listdir('%s/images' % (config['path']))
            except:
                pass #probably directory does not exist
            listdir.sort()
            complete = True
            lastfilename = ''
            lastfilename2 = ''
            c = 0
            for filename, url, uploader in images:
                filename2 = filename
                if len(filename2) > other['filenamelimit']:
                    filename2 = truncateFilename(other=other, filename=filename2)
                if filename2 not in listdir:
                    complete = False
                    lastfilename2 = lastfilename
                    lastfilename = filename #return always the complete filename, not the truncated
                    break
                c +=1
            print '%d images were found in the directory from a previous session' % (c)
            lastfilename2 = lastfilename # we resume from previous image, which may be corrupted by the previous session ctrl-c or abort
            if complete:
                #image dump is complete
                print 'Image dump was completed in the previous session'
            else:
                generateImageDump(config=config, other=other, images=images, start=lastfilename)
        
        if config['logs']:
            #fix
            pass
    else:
        print 'Trying generating a new dump into a new directory...'
        if config['xml']:
            titles += getPageTitles(config=config)
            saveTitles(config=config, titles=titles)
            generateXMLDump(config=config, titles=titles)
        if config['images']:
            images += getImageFilenamesURL(config=config) #fix add start like above
            saveImageFilenamesURL(config=config, images=images)
            generateImageDump(config=config, other=other, images=images)
        if config['logs']:
            saveLogs(config=config)
    
    #save index.php as html, for license details at the bootom of the page
    f = urllib.urlopen(config['index'])
    raw = f.read()
    raw = removeIP(raw=raw)
    f = open('%s/index.html' % (config['path']), 'w')
    f.write(raw)
    f.close()
    
    #save special:Version as html, for extensions details
    f = urllib.urlopen('%s?title=Special:Version' % (config['index']))
    raw = f.read()
    raw = removeIP(raw=raw)
    f = open('%s/Special:Version.html' % (config['path']), 'w')
    f.write(raw)
    f.close()
    
    bye()

if __name__ == "__main__":
    main()