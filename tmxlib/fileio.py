
import os
import base64
import gzip
import zlib
import array
import struct
import binascii
import StringIO

from lxml import etree

import tmxlib

parser = etree.XMLParser(remove_comments=True)

def read_write_base(read_func, write_func):
    class ReadWriteBase(object):
        @classmethod
        def open(cls, filename, base_path=None):
            if not base_path:
                base_path = os.path.dirname(os.path.abspath(filename))
            with open(filename, 'r') as fileobj:
                return cls.load(fileobj.read(), base_path)

        @classmethod
        def load(cls, string, base_path=None):
            tree = etree.XML(string, parser=parser)
            return cls.from_element(tree, base_path)

        @classmethod
        def from_element(cls, element, base_path=None):
            return read_func(cls, element, base_path)

        def save(self, filename, base_path=None):
            if not base_path:
                base_path = os.path.dirname(os.path.abspath(filename))
            with open(filename, 'w') as fileobj:
                fileobj.write(self.dump(base_path))

        def dump(self, base_path=None):
            return etree.tostring(self.to_element(base_path),
                    pretty_print=True, xml_declaration=True, encoding='UTF-8')

        def to_element(self, base_path=None, **kwargs):
            return write_func(self, base_path, **kwargs)

    return ReadWriteBase

def read_map(cls, root, base_path):
    assert root.tag == 'map'
    assert root.attrib.pop('version') == '1.0', 'Bad TMX file version'

    tile_data = []
    args = dict(
            size=(int(root.attrib.pop('width')),
                    int(root.attrib.pop('height'))),
            tile_size=(int(root.attrib.pop('tilewidth')),
                    int(root.attrib.pop('tileheight'))),
            orientation=root.attrib.pop('orientation'),
            base_path=base_path,
        )
    assert not root.attrib, 'Unexpected map attributes: %s' % root.attrib
    map = tmxlib.Map(**args)
    for elem in root:
        if elem.tag == 'properties':
            map.properties.update(read_properties(elem))
        elif elem.tag == 'tileset':
            tileset = tmxlib.Tileset.from_element(elem, base_path)
            map.tilesets.append(tileset)
            assert tileset.first_gid(map) == tileset._read_first_gid
        elif elem.tag == 'layer':
            map.layers.append(tile_layer_from_element(elem, map))
        elif elem.tag == 'objectgroup':
            map.layers.append(object_layer_from_element(elem, map))
        else:
            assert False, 'Unknown tag %s' % elem.tag
    return map

def write_map(map, base_path):
    elem = etree.Element('map', attrib=dict(
            version='1.0',
            orientation=map.orientation,
            width=str(map.width),
            height=str(map.height),
            tilewidth=str(map.tile_width),
            tileheight=str(map.tile_height),
        ))
    append_properties(elem, map.properties)
    for tileset in map.tilesets:
        elem.append(tileset.to_element(base_path,
                first_gid=tileset.first_gid(map)))
    for layer in map.layers:
        elem.append(layer_to_element(layer))
    return elem

def read_tileset(cls, elem, base_path):
    source = elem.attrib.pop('source', None)
    if source:
        # XXX: Return a proxy object?
        if base_path is None and not os.path.isabs(source):
            raise ValueError(
                'Cannot load external tileset from relative path %s' %
                    source)
        elif base_path:
            real_source = os.path.join(base_path, source)
        else:
            real_source = source
        first_gid = int(elem.attrib.pop('firstgid'))
        assert not elem.attrib, (
                'Unexpected tileset attributes: %s' % elem.attrib)
        tileset = cls.open(real_source)
        tileset._read_first_gid = first_gid
        tileset.source = source
        return tileset
    tileset = cls._image_tileset_class(name=elem.attrib.pop('name'),
            tile_size=(int(elem.attrib.pop('tilewidth')),
                int(elem.attrib.pop('tileheight'))),
            margin=int(elem.attrib.pop('margin', 0)),
            spacing=int(elem.attrib.pop('spacing', 0)),
        )
    tileset._read_first_gid = int(elem.attrib.pop('firstgid', 0))
    assert not elem.attrib, (
            'Unexpected tileset attributes: %s' % elem.attrib)
    for subelem in elem:
        if subelem.tag == 'image':
            assert tileset.image == None
            tileset.image = tmxlib.Image.from_element(subelem, base_path)
        elif subelem.tag == 'tile':
            id = int(subelem.attrib.pop('id'))
            for subsubelem in subelem:
                if subsubelem.tag == 'properties':
                    props = tileset.tile_properties[id]
                    props.update(read_properties(subsubelem))
                else:
                    assert False, 'Unknown tag %s' % subelem.tag
        else:
            assert False, 'Unknown tag %s' % subelem.tag
    return tileset

def write_tileset(tileset, base_path, first_gid=None):
    if tileset.source is not None:
        attrib = dict(
                source=tileset.source,
            )
        if first_gid:
            attrib['firstgid'] = str(first_gid)
        return etree.Element('tileset', attrib=attrib)
    else:
        attrib = dict(
                name=tileset.name,
                tileheight=str(tileset.tile_height),
                tilewidth=str(tileset.tile_width),
            )
        if first_gid:
            attrib['firstgid'] = str(first_gid)
        element = etree.Element('tileset', attrib=attrib)
        if tileset.spacing:
            element.attrib['spacing'] = str(tileset.spacing)
        if tileset.margin:
            element.attrib['margin'] = str(tileset.margin)
        if tileset.image:
            image = tileset.image.to_element()
            element.append(image)
        for tile_no, props in sorted(tileset.tile_properties.items()):
            if props:
                tile_elem = etree.Element('tile',
                        attrib=dict(id=str(tile_no)))
                element.append(tile_elem)
                append_properties(tile_elem, props)
        return element

def read_image(cls, elem, base_path):
    trans = elem.attrib.pop('trans', None)
    if trans:
        trans = from_rgb(trans)
    image = cls(
            source=elem.attrib.pop('source'),
            trans=trans,
            size=(int(elem.attrib.pop('width', 0)),
                    int(elem.attrib.pop('height', 0))),
        )
    assert not elem.attrib, (
        'Unexpected image attributes: %s' % elem.attrib)
    return image

def write_image(image, base_path):
    element = etree.Element('image', attrib=dict(source=image.source))
    if image.height:
        element.attrib['height'] = str(image.height)
    if image.width:
        element.attrib['width'] = str(image.width)
    if image.trans:
        element.attrib['trans'] = to_rgb(image.trans)
    return element

def tile_layer_from_element(elem, map):
    layer = tmxlib.ArrayMapLayer(map, elem.attrib.pop('name'),
            opacity=float(elem.attrib.pop('opacity', 1)),
            visible=bool(int(elem.attrib.pop('visible', 1))))
    layer_size = (int(elem.attrib.pop('width')),
            int(elem.attrib.pop('height')))
    assert layer_size == map.size
    assert not elem.attrib, (
        'Unexpected tile layer attributes: %s' % elem.attrib)
    data_set = False
    for subelem in elem:
        if subelem.tag == 'properties':
            layer.properties.update(read_properties(subelem))
        elif subelem.tag == 'data':
            assert data_set is False
            data = subelem.text
            encoding = subelem.attrib.pop('encoding')
            if encoding == 'base64':
                data = base64.b64decode(data)
                layer.encoding = 'base64'
            else:
                assert False, 'Bad encoding %s' % encoding
            compression = subelem.attrib.pop('compression', None)
            if compression == 'gzip':
                filelike = StringIO.StringIO(data)
                with gzip.GzipFile(fileobj=filelike) as gzfile:
                    data = gzfile.read()
                layer.compression = 'gzip'
            elif compression == 'zlib':
                data = zlib.decompress(data)
                layer.compression = 'zlib'
            elif compression:
                    assert False, (
                            'Bad compression %s' % compression)
            else:
                layer.compression = None
            layer.data = array.array('l', [(
                        ord(a) +
                        (ord(b) << 8) +
                        (ord(c) << 16) +
                        (ord(d) << 24)) for
                    a, b, c, d in
                    zip(*(data[x::4] for x in range(4)))])
            data_set = True
        else:
            assert False, 'Unknown tag %s' % subelem.tag
    assert data_set
    return layer

def layer_to_element(layer):
    if layer.type == 'objects':
        return object_layer_to_element(layer)
    elif layer.type == 'tiles':
        return tile_layer_to_element(layer)
    else:
        raise ValueError(layer.type)

def tile_layer_to_element(layer):
    element = etree.Element('layer', attrib=dict(
            name=layer.name,
            width=str(layer.map.width),
            height=str(layer.map.height),
        ))
    if not layer.visible:
        element.attrib['visible'] = '0'
    if layer.opacity != 1:
        element.attrib['opacity'] = str(layer.opacity)

    append_properties(element, layer.properties)

    # XXX: Make this yet faster
    data = layer.data
    data = struct.pack('<%sI' % len(data), *data)

    compression = getattr(layer, 'compression', 'zlib')
    encoding = getattr(layer, 'encoding', 'base64')
    extra_attrib = {}
    if compression:
        extra_attrib['compression'] = compression
    if encoding:
        extra_attrib['encoding'] = encoding

    if compression == 'gzip':
        io = StringIO.StringIO()
        gzfile = gzip.GzipFile(fileobj=io, mode='wb',
                mtime=getattr(layer, 'mtime', None))
        gzfile.write(data)
        gzfile.close()
        data = io.getvalue()
    elif compression == 'zlib':
        data = zlib.compress(data)
        extra_attrib['compression'] = 'zlib'
    elif compression:
        raise ValueError('Bad compression: %s', compression)
    if encoding == 'base64':
        data = base64.b64encode(data)
        extra_attrib['encoding'] = 'base64'
    else:
        raise ValueError('Bad encoding: %s', encoding)
    data_elem = etree.Element('data', attrib=extra_attrib)
    data_elem.text = data
    element.append(data_elem)
    return element

def object_layer_from_element(elem, map):
    layer = tmxlib.ObjectLayer(map, elem.attrib.pop('name'),
            opacity=float(elem.attrib.pop('opacity', 1)),
            visible=bool(int(elem.attrib.pop('visible', 1))))
    layer_size = (int(elem.attrib.pop('width')),
            int(elem.attrib.pop('height')))
    assert layer_size == map.size
    assert not elem.attrib, (
        'Unexpected object layer attributes: %s' % elem.attrib)
    for subelem in elem:
        if subelem.tag == 'properties':
            layer.properties.update(read_properties(subelem))
        elif subelem.tag == 'object':
            kwargs = dict(
                    pos=(
                            int(subelem.attrib.pop('x')),
                            int(subelem.attrib.pop('y'))),
                    layer=layer,
                )
            def put(attr_type, attr_name, arg_name):
                attr = subelem.attrib.pop(attr_name, None)
                if attr is not None:
                    kwargs[arg_name] = attr_type(attr)
            put(int, 'gid', 'value')
            put(unicode, 'name', 'name')
            put(unicode, 'type', 'type')
            width = subelem.attrib.pop('width', None)
            height = subelem.attrib.pop('height', None)
            if width is not None or height is not None:
                kwargs['size'] = int(width), int(height)
            assert not subelem.attrib, (
                'Unexpected object attributes: %s' % subelem.attrib)
            layer.append(tmxlib.MapObject(**kwargs))
    return layer

def object_layer_to_element(layer):
    element = etree.Element('objectgroup', attrib=dict(
            name=layer.name,
            width=str(layer.map.width),
            height=str(layer.map.height),
        ))
    if not layer.visible:
        element.attrib['visible'] = '0'
    if layer.opacity != 1:
        element.attrib['opacity'] = str(layer.opacity)

    append_properties(element, layer.properties)

    for object in layer:
        attrib = dict(x=str(object.x), y=str(object.y))
        if object.value:
            attrib['gid'] = str(object.value)
        if object.name:
            attrib['name'] = str(object.name)
        if object.type:
            attrib['type'] = str(object.type)
        if object.size != (0, 0) and not (object.tileset_tile and
                object.tileset_tile.size == object.size):
            attrib['width'] = str(object.width)
            attrib['height'] = str(object.height)
        obj_element = etree.Element('object', attrib=attrib)
        element.append(obj_element)

    return element

def read_properties(elem):
    assert elem.tag == 'properties'
    properties = {}
    assert not elem.attrib, (
            'Unexpected properties attributes: %s' % elem.attrib)
    for prop in elem:
        assert prop.tag == 'property'
        name = prop.attrib.pop('name')
        value = prop.attrib.pop('value')
        properties[name] = value
        assert not prop.attrib, (
                'Unexpected property attributes: %s' % prop.attrib)
    return properties

def append_properties(parent, props):
    if props:
        element = etree.Element('properties')
        for key, value in props.items():
            element.append(etree.Element('property', attrib=dict(
                    name=key,
                    value=value,
                )))
        parent.append(element)


def from_rgb(string):
    if string.startswith('#'):
        string = string[1:]
    if len(string) == 3:
        parts = string[0] * 2, string[1] * 2, string[2] * 2
    elif len(string) == 6:
        parts = string[0:2], string[2:4], string[4:6]
    return tuple(ord(binascii.unhexlify(p)) for p in parts)

def to_rgb(rgb):
    print rgb
    return ''.join(hex(p)[2:].ljust(2, '0') for p in rgb)
