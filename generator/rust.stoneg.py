from contextlib import nested

from stone import data_type
from stone.generator import CodeGenerator
from stone.target.helpers import (
    fmt_pascal,
    fmt_underscores,
)

RUST_RESERVED_WORDS = [
    "abstract", "alignof", "as", "become", "box", "break", "const", "continue", "crate", "do",
    "else", "enum", "extern", "false", "final", "fn", "for", "if", "impl", "in", "let", "loop",
    "macro", "match", "mod", "move", "mut", "offsetof", "override", "priv", "proc", "pub", "pure",
    "ref", "return", "Self", "self", "sizeof", "static", "struct", "super", "trait", "true", "type",
    "typeof", "unsafe", "unsized", "use", "virtual", "where", "while", "yield",

    # Also include names of types that are in the prelude:
    "Copy", "Send", "Sized", "Sync", "Drop", "Fn", "FnMut", "FnOnce", "drop", "Box", "ToOwned",
    "Clone", "PartialEq", "PartialOrd", "Eq", "Ord", "AsRef", "AsMut", "Into", "From", "Default",
    "Iterator", "Extend", "IntoIterator", "DoubleEndedIterator", "ExactSizeIterator", "Option",
    "Some", "None", "Result", "Ok", "Err", "SliceConcatExt", "String", "ToString", "Vec",
]

class RustGenerator(CodeGenerator):
    def __init__(self, target_folder_path, args):
        super(RustGenerator, self).__init__(target_folder_path, args)
        self._modules = []
        self.preserve_aliases = True

    # File Generators

    def generate(self, api):
        for namespace in api.namespaces.values():
            self._emit_namespace(namespace)
        self._generate_mod_file()

    def _generate_mod_file(self):
        with self.output_to_relative_path('mod.rs'):
            self._emit_header()
            for module in self._modules:
                self.emit(u'pub mod {};'.format(module))

    # Type Emitters

    def _emit_namespace(self, namespace):
        with self.output_to_relative_path(namespace.name + '.rs'):
            self._current_namespace = namespace.name
            self._emit_header()

            if namespace.doc is not None:
                self.emit_wrapped_text(namespace.doc, prefix=u'//! ', width=100)
                self.emit()

            self.emit(u'use serde::ser::SerializeStruct;')
            self.emit()

            for alias in namespace.aliases:
                self._emit_alias(alias)
            if namespace.aliases:
                self.emit()

            for fn in namespace.routes:
                self._emit_route(namespace.name, fn)

            for typ in namespace.data_type_by_name.values():
                if isinstance(typ, data_type.Struct):
                    if typ.has_enumerated_subtypes():
                        self._emit_polymorphic_struct(typ)
                    else:
                        self._emit_struct(typ)
                elif isinstance(typ, data_type.Union):
                    self._emit_union(typ)
                else:
                    print('WARNING: unhandled type "{}" of field "{}"'.format(
                        type(typ).__name__,
                        typ.name))

        self._modules.append(namespace.name)

    def _emit_header(self):
        self.emit(u'// DO NOT EDIT')
        self.emit(u'// This file was generated by Stone')
        self.emit()
        self.emit(u'#![allow(')
        self.emit(u'    unknown_lints,  // keep rustc from complaining about clippy lints')
        self.emit(u'    identity_op,    // due to a bug with serde + clippy')
        self.emit(u'    too_many_arguments,')
        self.emit(u'    large_enum_variant,')
        self.emit(u'    doc_markdown,')
        self.emit(u')]')
        self.emit()

    def _emit_struct(self, struct):
        struct_name = self._struct_name(struct)
        self._emit_doc(struct.doc)
        self.emit(u'#[derive(Debug)]')
        with self.block(u'pub struct {}'.format(struct_name)):
            for field in struct.all_fields:
                self._emit_doc(field.doc)
                self.emit(u'pub {}: {},'.format(
                    self._field_name(field),
                    self._rust_type(field.data_type)))
        self.emit()

        if not struct.all_required_fields:
            self._impl_default_for_struct(struct)
            self.emit()

        if struct.all_required_fields:
            with self._impl_struct(struct):
                if struct.all_required_fields:
                    self._emit_new_for_struct(struct)
                self.emit()
            self.emit()

        self._impl_serde_for_struct(struct)

    def _emit_polymorphic_struct(self, struct):
        enum_name = self._enum_name(struct)
        self._emit_doc(struct.doc)
        self.emit(u'#[derive(Debug)]')
        with self.block(u'pub enum {}'.format(enum_name)):
            for subtype in struct.get_enumerated_subtypes():
                self.emit(u'{}({}),'.format(
                    self._enum_variant_name(subtype.data_type),
                    self._rust_type(subtype.data_type)))
            if struct.is_catch_all():
                # TODO implement this
                print(u'WARNING: open unions are not implemented yet: {}::{}'.format(
                    struct.namespace.name,
                    struct.name))
                self.emit(u'_Unknown(::serde_json::value::Value),')
        self.emit()

        self._impl_serde_for_polymorphic_struct(struct)

    def _emit_union(self, union):
        enum_name = self._enum_name(union)
        self._emit_doc(union.doc)
        self.emit(u'#[derive(Debug)]')
        with self.block(u'pub enum {}'.format(enum_name)):
            for field in union.all_fields:
                self._emit_doc(field.doc)
                variant_name = self._enum_variant_name(field)
                if isinstance(field.data_type, data_type.Void):
                    self.emit(u'{},'.format(variant_name))
                else:
                    self.emit(u'{}({}),'.format(variant_name, self._rust_type(field.data_type)))
        self.emit()

        self._impl_serde_for_union(union)

        if union.name.endswith('Error'):
            self._impl_error(enum_name)

    def _emit_route(self, ns, fn):
        route_name = self._route_name(fn)
        self._emit_doc(fn.doc)
        host = fn.attrs.get('host', 'api')
        if host == 'api':
            endpoint = u'::client_trait::Endpoint::Api'
        elif host == 'content':
            endpoint = u'::client_trait::Endpoint::Content'
        elif host == 'notify':
            endpoint = u'::client_trait::Endpoint::Notify'
        else:
            print(u'ERROR: unsupported endpoint: {}'.format(host))
            return

        # TODO: do something about these extremely long lines

        style = fn.attrs.get('style', 'rpc')
        if style == 'rpc':
            with self.block(u'pub fn {}(client: &::client_trait::HttpClient, arg: &{}) -> ::Result<Result<{}, {}>>'.format(
                    route_name,
                    self._rust_type(fn.arg_data_type),
                    self._rust_type(fn.result_data_type),
                    self._rust_type(fn.error_data_type))):
                self.emit(u'::client_helpers::request(client, {}, "{}/{}", arg, None)'.format(
                    endpoint,
                    ns,
                    fn.name))
        elif style == 'download':
            with self.block(u'pub fn {}(client: &::client_trait::HttpClient, arg: &{}, range_start: Option<u64>, range_end: Option<u64>) -> ::Result<Result<::client_trait::HttpRequestResult<{}>, {}>>'.format(
                    route_name,
                    self._rust_type(fn.arg_data_type),
                    self._rust_type(fn.result_data_type),
                    self._rust_type(fn.error_data_type))):
                self.emit(u'::client_helpers::request_with_body(client, {}, "{}/{}", arg, None, range_start, range_end)'.format(
                    endpoint,
                    ns,
                    fn.name))
        elif style == 'upload':
            with self.block(u'pub fn {}(client: &::client_trait::HttpClient, arg: &{}, body: Vec<u8>) -> ::Result<Result<::client_trait::HttpRequestResult<{}>, {}>>'.format(
                    route_name,
                    self._rust_type(fn.arg_data_type),
                    self._rust_type(fn.result_data_type),
                    self._rust_type(fn.error_data_type))):
                self.emit(u'::client_helpers::request_with_body(client, {}, "{}/{}", arg, Some(body), None, None)'.format(
                    endpoint,
                    ns,
                    fn.name))
        else:
            print(u'ERROR: unknown route style: {}'.format(style))
            return
        self.emit()

    def _emit_alias(self, alias):
        alias_name = self._alias_name(alias)
        self.emit(u'pub type {} = {};'.format(alias_name, self._rust_type(alias.data_type)))

    # Serialization

    def _impl_serde_for_struct(self, struct):
        type_name = self._struct_name(struct)
        with self._impl_deserialize(self._struct_name(struct)):
            self.emit(u'// struct deserializer')
            self.emit(u'use serde::de::{self, MapAccess, Visitor};')
            self.emit(u'struct StructVisitor;')
            with self.block(u'impl<\'de> Visitor<\'de> for StructVisitor'):
                self.emit(u'type Value = {};'.format(type_name))
                with self.block(u'fn expecting(&self, f: &mut ::std::fmt::Formatter) -> ::std::fmt::Result'):
                    self.emit(u'f.write_str("a {} struct")'.format(struct.name))
                with self.block(u'fn visit_map<V: MapAccess<\'de>>(self, mut map: V) -> Result<Self::Value, V::Error>'):
                    for field in struct.all_fields:
                        self.emit(u'let mut {} = None;'.format(self._field_name(field)))
                    with nested(self.block(u'while let Some(key) = map.next_key()?'), self.block(u'match key')):
                        for field in struct.all_fields:
                            field_name = self._field_name(field)
                            with self.block(u'"{}" =>'.format(field.name)):
                                with self.block(u'if {}.is_some()'.format(field_name)):
                                    self.emit(u'return Err(de::Error::duplicate_field("{}"));'.format(field.name))
                                self.emit(u'{} = Some(map.next_value()?);'.format(field_name))
                        self.emit(u'_ => return Err(de::Error::unknown_field(key, FIELDS))')
                    with self.block(u'Ok({}'.format(type_name), delim=(u'{',u'})')):
                        for field in struct.all_fields:
                            field_name = self._field_name(field)
                            if isinstance(field.data_type, data_type.Nullable):
                                self.emit(u'{},'.format(field_name))
                            elif field.has_default:
                                # TODO: check if the default is a copy type (i.e. primitive) and don't make a lambda
                                self.emit(u'{}: {}.unwrap_or_else(|| {}),'.format(
                                    field_name, field_name, self._default_value(field)))
                            else:
                                self.emit(u'{}: {}.ok_or_else(|| de::Error::missing_field("{}"))?,'.format(
                                    field_name, field_name, field.name))
            self.generate_multiline_list(
                    list(u'"{}"'.format(field.name) for field in struct.all_fields),
                    before='const FIELDS: &\'static [&\'static str] = &',
                    after=';',
                    delim=(u'[',u']'),)
            self.emit(u'_deserializer.deserialize_struct("{}", FIELDS, StructVisitor)'.format(
                struct.name))
        self.emit()
        with self._impl_serialize(type_name):
            self.emit(u'// struct serializer')
            if not struct.all_fields:
                self.emit(u'serializer.serialize_unit_struct("{}")'.format(struct.name))
            else:
                self.emit(u'let mut s = serializer.serialize_struct("{}", {})?;'.format(
                    struct.name,
                    len(struct.all_fields)))
                for field in struct.all_fields:
                    self.emit(u's.serialize_field("{}", &self.{})?;'.format(
                        field.name,
                        self._field_name(field)))
                self.emit(u's.end()')
        self.emit()

    def _impl_serde_for_polymorphic_struct(self, struct):
        with self._impl_deserialize(self._enum_name(struct)):
            self.emit(u'unimplemented!()')
        self.emit()
        type_name = self._enum_name(struct)
        with self._impl_serialize(self._struct_name(struct)):
            self.emit(u'// polymorphic struct serializer')
            with self.block(u'match *self'):
                i = 0
                for subtype in struct.get_enumerated_subtypes():
                    variant_name = self._enum_variant_name(subtype.data_type)
                    with self.block(u'{}::{}(ref x) =>'.format(type_name, variant_name)):
                        self.emit(u'let mut s = serializer.serialize_struct("{}", {})?;'.format(
                            type_name, len(subtype.data_type.all_fields) + 1))
                        self.emit(u's.serialize_field(".tag", "{}")?;'.format(subtype.name))
                        for field in subtype.data_type.all_fields:
                            self.emit(u's.serialize_field("{}", &x.{})?;'.format(
                                field.name,
                                self._field_name(field)))
                        self.emit(u's.end()')
                if struct.is_catch_all():
                    self.emit(u'{}::_Unknown(_) => Err(::serde::ser::Error::custom("cannot serialize unknown variant"))'.format(
                        type_name))
        self.emit()

    def _impl_serde_for_union(self, union):
        type_name = self._enum_name(union)
        with self._impl_deserialize(type_name):
            self.emit(u'unimplemented!()')
        self.emit()
        with self._impl_serialize(type_name):
            self.emit(u'// union serializer')
            with self.block(u'match *self'):
                for field in union.all_fields:
                    variant_name = self._enum_variant_name(field)
                    if isinstance(field.data_type, data_type.Void):
                        with self.block(u'{}::{} =>'.format(type_name, variant_name)):
                            self.emit(u'// unit')
                            self.emit(u'let mut s = serializer.serialize_struct("{}", 1)?;'.format(union.name))
                            self.emit(u's.serialize_field(".tag", "{}")?;'.format(field.name))
                            self.emit(u's.end()')
                    else:
                        needs_x = not (isinstance(field.data_type, data_type.Struct) and not field.data_type.all_fields)
                        ref_x = 'ref x' if needs_x else '_'
                        with self.block(u'{}::{}({}) =>'.format(type_name, variant_name, ref_x)):
                            if isinstance(field.data_type, data_type.Union) or \
                                    (isinstance(field.data_type, data_type.Struct) and \
                                        field.data_type.has_enumerated_subtypes()):
                                self.emit(u'// union or polymporphic struct')
                                self.emit(u'let mut s = serializer.serialize_struct("{}", 2)?;')
                                self.emit(u's.serialize_field(".tag", "{}")?;'.format(field.name))
                                self.emit(u's.serialize_field("{}", x)?;'.format(field.name))
                                self.emit(u's.end()')
                            elif isinstance(field.data_type, data_type.Struct):
                                self.emit(u'// struct')
                                self.emit(u'let mut s = serializer.serialize_struct("{}", {})?;'.format(
                                    union.name,
                                    len(field.data_type.all_fields) + 1))
                                self.emit(u's.serialize_field(".tag", "{}")?;'.format(field.name))
                                for subfield in field.data_type.all_fields:
                                    self.emit(u's.serialize_field("{}", &x.{})?;'.format(
                                        subfield.name,
                                        self._field_name(subfield)))
                                self.emit(u's.end()')
                            else:
                                self.emit(u'// primitive')
                                self.emit(u'let mut s = serializer.serialize_struct("{}", 2)?;')
                                self.emit(u's.serialize_field(".tag", "{}")?;'.format(field.name))
                                self.emit(u's.serialize_field("{}", x)?;'.format(field.name))
                                self.emit(u's.end()')
        self.emit()

    # Helpers

    def _emit_doc(self, doc_string):
        if doc_string is not None:
            self.emit_wrapped_text(doc_string, prefix=u'/// ', width=100)

    def _impl_deserialize(self, type_name):
        return nested(self.block(u'impl<\'de> ::serde::de::Deserialize<\'de> for {}'.format(type_name)),
            self.block(u'fn deserialize<D: ::serde::de::Deserializer<\'de>>(_deserializer: D) -> Result<Self, D::Error>'))

    def _impl_serialize(self, type_name):
        return nested(self.block(u'impl ::serde::ser::Serialize for {}'.format(type_name)),
            self.block(u'fn serialize<S: ::serde::ser::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error>'))

    def _impl_default_for_struct(self, struct):
        struct_name = self._struct_name(struct)
        with self.block(u'impl Default for {}'.format(struct_name)):
            with self.block(u'fn default() -> Self'):
                with self.block(struct_name):
                    for field in struct.all_fields:
                        self.emit(u'{}: {},'.format(
                            self._field_name(field), self._default_value(field)))

    def _impl_struct(self, struct):
        return self.block(u'impl {}'.format(self._struct_name(struct)))

    def _emit_new_for_struct(self, struct):
        struct_name = self._struct_name(struct)
        args = u''
        for field in struct.all_required_fields:
            args += u'{}: {}, '.format(self._field_name(field), self._rust_type(field.data_type))
        args = args[:-2]

        with self.block(u'pub fn new({}) -> Self'.format(args)):
            with self.block(struct_name):
                for field in struct.all_required_fields:
                    self.emit(u'{},'.format(self._field_name(field))) # shorthand assignment
                for field in struct.all_optional_fields:
                    self.emit(u'{}: {},'.format(self._field_name(field), self._default_value(field)))

        for field in struct.all_optional_fields:
            self.emit()
            field_name = self._field_name(field)
            with self.block(u'pub fn {}(mut self, value: {}) -> Self'.format(
                    field_name,
                    self._rust_type(field.data_type))):
                self.emit(u'self.{} = value;'.format(field_name))
                self.emit(u'self')

    def _default_value(self, field):
        if isinstance(field.data_type, data_type.Nullable):
            return u'None'
        elif data_type.is_numeric_type(data_type.unwrap_aliases(field.data_type)[0]):
            return field.default
        elif isinstance(field.default, data_type.TagRef):
            default_variant = None
            for variant in field.default.union_data_type.all_fields:
                if variant.name == field.default.tag_name:
                    default_variant = variant
            if default_variant is None:
                print('ERROR: didn\'t find matching variant: {}'.format(field.default.tag_name))
                for variant in field.default.union_data_type.fields:
                    print(u'\tvariant.name = {}'.format(variant.name))
                default_variant = variant
            return u'{}::{}'.format(
                self._rust_type(field.default.union_data_type),
                self._enum_variant_name(default_variant))
        elif isinstance(field.data_type, data_type.Boolean):
            if field.default:
                return u'true'
            else:
                return u'false'
        elif isinstance(field.data_type, data_type.String):
            if not field.default:
                return u'String::new()'
            else:
                return u'"{}".to_owned()'.format(field.default)
        else:
            print(u'WARNING: unhandled default value {}'.format(field.default))
            print(u'    in field: {}'.format(field))
            if isinstance(field.data_type, data_type.Alias):
                print(u'    unwrapped alias: {}'.format(data_type.unwrap_aliases(field.data_type)[0]))
            return field.default

    def _needs_explicit_default(self, field):
        return field.has_default \
                and not (isinstance(field, data_type.Nullable) \
                    or (isinstance(field.data_type, data_type.Boolean) and not field.default))

    def _impl_error(self, type_name):
        with self.block(u'impl ::std::error::Error for {}'.format(type_name)):
            with self.block(u'fn description(&self) -> &str'):
                self.emit(u'"{}"'.format(type_name))
        self.emit()
        with self.block(u'impl ::std::fmt::Display for {}'.format(type_name)):
            with self.block(u'fn fmt(&self, f: &mut ::std::fmt::Formatter) -> ::std::fmt::Result'):
                self.emit(u'write!(f, "{:?}", *self)')
        self.emit()

    # Naming Rules

    def _rust_type(self, typ):
        if isinstance(typ, data_type.Nullable):
            return u'Option<{}>'.format(self._rust_type(typ.data_type))
        elif isinstance(typ, data_type.Void):       return u'()'
        elif isinstance(typ, data_type.Bytes):      return u'Vec<u8>'
        elif isinstance(typ, data_type.Int32):      return u'i32'
        elif isinstance(typ, data_type.UInt32):     return u'u32'
        elif isinstance(typ, data_type.Int64):      return u'i64'
        elif isinstance(typ, data_type.UInt64):     return u'u64'
        elif isinstance(typ, data_type.Float32):    return u'f32'
        elif isinstance(typ, data_type.Float64):    return u'f64'
        elif isinstance(typ, data_type.Boolean):    return u'bool'
        elif isinstance(typ, data_type.String):     return u'String'
        elif isinstance(typ, data_type.Timestamp):  return u'String /*Timestamp*/' # TODO
        elif isinstance(typ, data_type.List):
            return u'Vec<{}>'.format(self._rust_type(typ.data_type))
        elif isinstance(typ, data_type.Map):
            return u'HashMap<{}, {}>'.format(
                self._rust_type(typ.key_data_type),
                self._rust_type(typ.value_data_type))
        elif isinstance(typ, data_type.Alias):
            if typ.namespace.name == self._current_namespace:
                return self._alias_name(typ)
            else:
                return u'super::{}::{}'.format(
                    self._namespace_name(typ.namespace),
                    self._alias_name(typ))
        elif isinstance(typ, data_type.UserDefined):
            if isinstance(typ, data_type.Struct):
                name = self._struct_name(typ)
            elif isinstance(typ, data_type.Union):
                name = self._enum_name(typ)
            else:
                print(u'ERROR: user-defined type "{}" is neither Struct nor Union???'.format(typ))
                return u'()'
            if typ.namespace.name == self._current_namespace:
                return name
            else:
                return u'super::{}::{}'.format(
                    self._namespace_name(typ.namespace),
                    name)
        else:
            print(u'ERROR: unhandled type "{}"'.format(typ))
            return u'()'

    def _namespace_name(self, ns):
        name = fmt_underscores(ns.name)
        if name in RUST_RESERVED_WORDS:
            name += '_namespace'
        return name

    def _struct_name(self, struct):
        name = fmt_pascal(struct.name)
        if name in RUST_RESERVED_WORDS:
            name += 'Struct'
        return name

    def _enum_name(self, union):
        name = fmt_pascal(union.name)
        if name in RUST_RESERVED_WORDS:
            name += 'Union'
        return name

    def _field_name(self, field):
        name = fmt_underscores(field.name)
        if name in RUST_RESERVED_WORDS:
            name += '_field'
        return name

    def _enum_variant_name(self, field):
        name = fmt_pascal(field.name)
        if name in RUST_RESERVED_WORDS:
            name += 'Variant'
        return name

    def _route_name(self, route):
        name = fmt_underscores(route.name)
        if name in RUST_RESERVED_WORDS:
            name += '_route'
        return name

    def _alias_name(self, alias):
        name = fmt_pascal(alias.name)
        if name in RUST_RESERVED_WORDS:
            name += 'Alias'
        return name
