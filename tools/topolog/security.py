"""Constrain imported evidence and report files to their selected directory."""
import os
from pathlib import Path,PureWindowsPath
import re
import stat
import xml.etree.ElementTree as ET


def relative_name(name):
    if not isinstance(name,str) or not name or any(ord(c)<32 or ord(c)==127 for c in name):
        raise ValueError('Invalid evidence filename')
    normalized=name.replace('\\','/')
    if normalized.startswith('/') or PureWindowsPath(name).drive or re.search(r'[:<>"|?*]',normalized):
        raise ValueError('Absolute paths and NTFS streams are not supported')
    parts=normalized.split('/')
    if any(p in ('','.','..') or p.rstrip(' .')!=p or
           re.fullmatch(r'(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?',p,re.I) for p in parts):
        raise ValueError('Unsafe evidence filename')
    return normalized


def linked(path):
    try:
        data=path.lstat()
    except FileNotFoundError:return False
    return stat.S_ISLNK(data.st_mode) or bool(getattr(data,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',1024))


def safe_path(base,name):
    base=Path(base).resolve();name=relative_name(name);path=base
    for part in name.split('/'):
        path=path/part
        if linked(path):raise ValueError('Linked evidence files and directories are not supported')
    if not path.resolve().is_relative_to(base):raise ValueError('Evidence path escapes result folder')
    return path


def ensure_tree(root):
    root=Path(root)
    if linked(root):raise ValueError('Linked result directories are not supported')
    if not root.exists():return
    for directory,dirs,files in os.walk(root,followlinks=False):
        for name in dirs+files:
            relative_name(name)
            if linked(Path(directory)/name):raise ValueError('Linked evidence files and directories are not supported')


def validate_report_paths(report,root):
    from .resultdir import records, project_dir, node_dir, node_dir_of
    ensure_tree(root)
    root=records(root)
    for project in report.get('projects',[]):
        if not project.get('project_id') and not project.get('nodes') and not any(i.get('file') for i in project.get('topology_images',{}).values()):continue
        for item in [project]+project.get('nodes',[]):
            if 'folder' in item and (not isinstance(item['folder'],str) or re.search(r'[/\\]',item['folder'])):
                raise ValueError('Evidence folder must be a single folder name')
        base=safe_path(root,project_dir(project))
        for image in project.get('topology_images',{}).values():
            if image.get('file'):safe_path(base,image['file'])
        for node in project.get('nodes',[]):
            folder=safe_path(base,node_dir(node))
            for command in node.get('commands',[]):
                if command.get('file'):safe_path(folder,command['file'])
            startup=node.get('startup_configuration',{})
            if startup.get('file'):safe_path(folder,startup['file'])
            for check in node.get('checks',[]):
                if check.get('evidence'):safe_path(folder,check['evidence'])
                for evidence in check.get('additional_evidence',[]):
                    safe_path(safe_path(base,node_dir_of(project,evidence.get('node_id',''))),evidence.get('file',''))


def passive_svg(text):
    """Public review copies retain drawing elements, never active SVG content."""
    if len(text)>2*1024*1024 or re.search(r'<!\s*(?:DOCTYPE|ENTITY)',text,re.I):
        raise ValueError('Unsupported SVG declaration or size')
    root=ET.fromstring(text)
    allowed={'svg','g','path','rect','circle','ellipse','line','polyline','polygon','text','tspan',
             'title','desc','defs','linearGradient','radialGradient','stop','clipPath','mask','use'}
    namespace='http://www.w3.org/2000/svg'
    def tag(element):return element.tag.rsplit('}',1)[-1]
    if tag(root)!='svg':raise ValueError('Expected an SVG drawing')
    for element in root.iter():
        for child in list(element):
            if tag(child) not in allowed or (child.tag.startswith('{') and not child.tag.startswith('{'+namespace+'}')):
                element.remove(child)
        for attr,value in list(element.attrib.items()):
            name=attr.rsplit('}',1)[-1].lower()
            if name.startswith('on') or name in ('style','base') or \
               (name=='href' and not re.fullmatch(r'#[A-Za-z_][A-Za-z0-9_.:-]*',value)) or \
               re.search(r'(?i)javascript:|data:|https?:|file:|@import',value) or \
               ('url(' in value.lower() and not re.fullmatch(r'url\(#[A-Za-z_][A-Za-z0-9_.:-]*\)',value)):
                del element.attrib[attr]
    ET.register_namespace('',namespace)
    return ET.tostring(root,encoding='unicode')


PNG_SIGNATURE=b'\x89PNG\r\n\x1a\n'
# Pixel data and colour information only; text, time and EXIF chunks can carry
# user names, paths or other context that a review copy should not publish.
PNG_KEEP={b'IHDR',b'PLTE',b'IDAT',b'IEND',b'tRNS',b'gAMA',b'cHRM',b'sRGB',b'iCCP',b'sBIT',b'pHYs',b'bKGD'}


def png_without_metadata(data):
    import struct,zlib
    if len(data)>32*1024*1024 or not data.startswith(PNG_SIGNATURE):
        raise ValueError('Expected a PNG image')
    out=bytearray(PNG_SIGNATURE);offset=8;ended=False
    while offset+12<=len(data):
        length,kind=struct.unpack('>I4s',data[offset:offset+8])
        end=offset+12+length
        if end>len(data):raise ValueError('Truncated PNG chunk')
        crc=struct.unpack('>I',data[end-4:end])[0]
        if zlib.crc32(data[offset+4:end-4])&0xffffffff!=crc:raise ValueError('Corrupt PNG chunk')
        if kind in PNG_KEEP:out+=data[offset:end]
        offset=end
        if kind==b'IEND':ended=True;break
    if not ended:raise ValueError('PNG end chunk missing')
    return bytes(out)


def safe_argument(value,limit=200):
    """Text passed as a value to a PowerShell -File script parameter."""
    value=str(value)
    if not value or len(value)>limit or value[0] in '-/' or any(ord(c)<32 or c in '"`' for c in value):
        raise ValueError('Unsupported characters in a value passed to the capture script')
    return value
