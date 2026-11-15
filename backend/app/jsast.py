"""Bounded, non-executing JavaScript contract recovery.

Lexical bindings, imported clients and simple local helpers are followed. Unknown
values and conditional writes stay symbolic; this is not a JavaScript interpreter.
"""
from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from tree_sitter import Language, Parser
import tree_sitter_javascript

METHODS = {'get', 'post', 'put', 'patch', 'delete', 'head', 'options'}
FUNCTIONS = {'function_declaration', 'function_expression', 'arrow_function', 'method_definition', 'generator_function'}
MAX_NODES = 1_000_000
MAX_SHAPES = 5000


@dataclass
class Function:
    node: object


class Analyzer:
    def __init__(self, source):
        self.raw = source.encode('utf-8')
        self.tree = Parser(Language(tree_sitter_javascript.language())).parse(self.raw)
        self.nodes, self.scopes, self.parents, self.bindings = [], {}, {0: None}, {0: {}}
        self.function_scopes, self.writes, self.exports, self.modules = {}, {}, {}, {}
        self.members = {}
        self.reducers, self.state_reads = {}, set()
        self.warnings, self.shapes, self.seen = [], [], set()
        self.budget = 1_000_000
        self.module_cache = {}
        self.current_callsite = None
        self.index()

    def text(self, n):
        return self.raw[n.start_byte:n.end_byte].decode('utf-8', errors='replace') if n else ''

    def unknown(self, n, reason=None):
        full = self.text(n)
        result = {'$expression': full[:600]}
        if len(full) > 600:
            result['$expression_truncated'] = True
            result['source_range'] = {'start_byte': n.start_byte, 'end_byte': n.end_byte}
        if reason:
            result['$reason'] = reason
        return result

    def bind(self, scope, name, node, position):
        self.bindings[scope].setdefault(name, []).append((position, node))

    def index(self):
        stack = [(self.tree.root_node, 0)]
        while stack:
            node, scope = stack.pop()
            if len(self.nodes) >= MAX_NODES:
                self.warnings.append('AST node limit reached; unvisited syntax remains.')
                break
            self.nodes.append(node)
            if node.type in FUNCTIONS:
                outer = scope
                scope = node.id
                self.parents[scope], self.bindings[scope] = outer, {}
                self.function_scopes[scope] = node
                name = node.child_by_field_name('name')
                if name:
                    self.bind(outer, self.text(name), node, -1)
                params = node.child_by_field_name('parameters')
                for p in params.named_children if params else []:
                    for item in self.walk(p):
                        if item.type in ('identifier', 'shorthand_property_identifier_pattern'):
                            self.bind(scope, self.text(item), item, -1)
            elif node.type == 'statement_block':
                outer, scope = scope, node.id
                self.parents[scope], self.bindings[scope] = outer, {}
            self.scopes[node.id] = scope
            if node.type == 'variable_declarator':
                name, val = node.child_by_field_name('name'), node.child_by_field_name('value')
                if name and name.type == 'identifier':
                    decl_scope = scope
                    if node.parent.type == 'variable_declaration':
                        while decl_scope not in self.function_scopes and self.parents[decl_scope] is not None:
                            decl_scope = self.parents[decl_scope]
                    self.bind(decl_scope, self.text(name), val if val is not None else name, node.end_byte)
            if node.type == 'import_statement':
                src = node.child_by_field_name('source')
                if src and self.string(src) in ('axios', 'cross-fetch', 'node-fetch'):
                    for child in self.walk(node):
                        if child.type == 'identifier':
                            self.bind(scope, self.text(child), 'axios' if self.string(src) == 'axios' else 'fetch', -1)
            stack.extend((c, scope) for c in reversed(node.named_children))
        # Webpack's module array and numeric module-object entries are inert syntax.
        module_arrays = [n for n in self.nodes if n.type == 'array' and len(n.named_children) >= 2 and
                         sum(c.type in FUNCTIONS for c in n.named_children) > len(n.named_children) * .8]
        largest_array = max(module_arrays, key=lambda n: len(n.named_children), default=None)
        for node in self.nodes:
            if node.type == 'switch_case' and node.named_children and node.named_children[0].type == 'string':
                self.reducers.setdefault(self.string(node.named_children[0]), []).append(node)
            if node.type == 'member_expression' and node.end_byte - node.start_byte < 500:
                import re
                self.state_reads.update(re.findall(r'globalData\.([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+)', self.text(node)))
            if largest_array is not None and node.id == largest_array.id:
                # Sparse webpack arrays contain omitted entries. named_children
                # would renumber modules and resolve the wrong implementation.
                index = 0
                for child in node.children:
                    if child.type == ',':
                        index += 1
                    elif child.type in FUNCTIONS:
                        self.modules[index] = child
            if node.type == 'pair':
                key, val = node.child_by_field_name('key'), node.child_by_field_name('value')
                if key and key.type == 'number' and val and val.type in FUNCTIONS:
                    self.modules[int(self.text(key))] = val
            if node.type == 'assignment_expression':
                left, right = node.child_by_field_name('left'), node.child_by_field_name('right')
                if left and right:
                    scope = self.scopes[node.id]
                    if left.type == 'identifier':
                        owner = self.owner(scope, self.text(left))
                        self.bind(owner, self.text(left), right, node.end_byte)
                    elif left.type == 'member_expression':
                        obj, _ = self.member_path(left)
                        if obj is not None:
                            self.writes.setdefault((self.owner(scope, self.text(obj)), self.text(obj)), []).append(node)
            # ES-module exports in webpack: n.d(exports, "publicName", () => fn).
            if node.type == 'call_expression':
                args = node.child_by_field_name('arguments')
                args = args.named_children if args else []
                fn = self.text(node.child_by_field_name('function'))
                if len(args) >= 2 and args[1].type == 'string':
                    import re
                    state = re.search(r'globalData\.([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)$', self.text(args[0]))
                    if state and self.string(args[1]):
                        self.state_reads.add(state.group(1) + '.' + self.string(args[1]))
                if fn.endswith('.d') and len(args) == 3 and args[1].type == 'string':
                    name = self.string(args[1])
                    returns = [n for n in self.walk(args[2]) if n.type == 'return_statement']
                    if len(returns) == 1 and returns[0].named_children:
                        self.exports.setdefault(name, []).append(returns[0].named_children[0])
                if len(args) == 3 and args[1].type == 'string':
                    target = args[2]
                    if target.type == 'parenthesized_expression':
                        target = target.named_children[0]
                    if target.type in FUNCTIONS:
                        self.members.setdefault(self.string(args[1]), []).append(target)
        for names in self.bindings.values():
            for values in names.values():
                values.sort(key=lambda x: x[0])

    @staticmethod
    def walk(node):
        stack = [node]
        while stack:
            n = stack.pop()
            yield n
            stack.extend(reversed(n.named_children))

    def owner(self, scope, name):
        while scope is not None:
            if name in self.bindings[scope]:
                return scope
            scope = self.parents[scope]
        return 0

    def string(self, n):
        text = self.text(n)
        try:
            return json.loads(text) if text.startswith('"') else ast.literal_eval(text)
        except (ValueError, SyntaxError, TypeError):
            return text[1:-1]

    def module_client(self, number, seen=()):
        if number in self.module_cache:
            return self.module_cache[number]
        if number in seen or len(seen) > 8 or number not in self.modules:
            return None
        node = self.modules[number]
        text = self.text(node)
        if all(x in text for x in ('.Axios=', '.CancelToken=', '.isCancel=')):
            self.module_cache[number] = 'axios'
            return 'axios'
        if '.fetch' in text and all(x in text for x in ('.Headers', '.Request', '.Response', '.exports')):
            self.module_cache[number] = 'fetch'
            return 'fetch'
        # A direct re-export only, never an arbitrary dependency in a large module.
        if len(text) < 160:
            import re
            m = re.search(r'\.exports\s*=\s*\w+\((\d+)\)', text)
            if m:
                return self.module_client(int(m[1]), (*seen, number))
        return None

    def lookup(self, node, name, context, depth):
        scope = self.owner(self.scopes.get(node.id, 0), name)
        entries = self.bindings[scope].get(name, [])
        eligible = [(p, n) for p, n in entries if p < node.start_byte]
        if not eligible:
            return self.unknown(node)
        target = None
        for _, candidate in reversed(eligible):
            guard = self.guard(candidate, context, depth)
            if guard is False:
                continue
            if guard is None:
                return {'$expression': name, '$reason': 'conditional assignment',
                        '$candidate': self.clean(self.value(candidate, context, depth + 1))}
            target = candidate
            break
        if target is None:
            return self.unknown(node)
        if isinstance(target, str):
            return {'$client': target}
        if target.type == 'identifier' and (scope, name) in context:
            result = context[(scope, name)]
        elif target.id == node.id:
            result = self.unknown(node)
        else:
            result = self.value(target, context, depth + 1)
        # Object writes retain conditional/open fields rather than asserting branches ran.
        for assignment in self.writes.get((scope, name), []):
            if assignment.end_byte >= node.start_byte:
                continue
            guard = self.guard(assignment.child_by_field_name('right'), context, depth)
            if guard is False:
                continue
            _, parts = self.member_path(assignment.child_by_field_name('left'))
            if parts:
                if not isinstance(result, dict):
                    result = {'$spread': result}
                result = self.set_member(result, parts, self.value(assignment.child_by_field_name('right'), context, depth + 1))
                if guard is None:
                    result['$conditional_writes'] = True
        return result

    def member_path(self, node):
        parts = []
        while node is not None and node.type == 'member_expression':
            parts.insert(0, self.text(node.child_by_field_name('property')))
            node = node.child_by_field_name('object')
        return (node, parts) if node is not None and node.type == 'identifier' else (None, [])

    def set_member(self, obj, parts, value):
        if len(parts) > 24:
            return {**obj, '$unresolved_write': True}
        result = dict(obj)
        if len(parts) == 1:
            result[parts[0]] = value
        else:
            child = result.get(parts[0], {})
            if not isinstance(child, dict):
                child = {'$spread': child}
            result[parts[0]] = self.set_member(child, parts[1:], value)
        return result

    def guard(self, node, context, depth):
        if isinstance(node, str) or node.parent is None or node.parent.type != 'assignment_expression':
            return True
        child, parent = node.parent, node.parent.parent
        while parent is not None and parent.type not in FUNCTIONS:
            condition, required = None, True
            if parent.type == 'binary_expression' and parent.child_by_field_name('right') == child:
                left, right = parent.child_by_field_name('left'), parent.child_by_field_name('right')
                op = self.raw[left.end_byte:right.start_byte].decode().strip()
                if op in ('&&', '||'):
                    condition, required = left, op == '&&'
            elif parent.type == 'if_statement':
                condition = parent.child_by_field_name('condition')
                required = parent.child_by_field_name('alternative') != child
            if condition is not None:
                value = self.value(condition, context, depth + 1)
                if not isinstance(value, bool):
                    return None
                if value != required:
                    return False
            child, parent = parent, parent.parent
        return True

    def binding_node(self, node):
        scope = self.owner(self.scopes.get(node.id, 0), self.text(node))
        entries = self.bindings[scope].get(self.text(node), [])
        eligible = [(p, n) for p, n in entries if p < node.start_byte]
        return eligible[-1][1] if eligible else None

    def function(self, node, depth=0):
        if not node or depth > 12:
            return None
        if node.type in FUNCTIONS:
            return Function(node)
        if node.type == 'parenthesized_expression':
            return self.function(node.named_children[0], depth + 1)
        if node.type == 'identifier':
            target = self.binding_node(node)
            if target is not None and not isinstance(target, str) and target.id != node.id:
                return self.function(target, depth + 1)
        return None

    def client(self, node, context=None, depth=0):
        """Resolve client identity without evaluating arbitrary helper return bodies."""
        if not node or depth > 12:
            return None
        name = self.text(node)
        if name in ('window.fetch', 'globalThis.fetch'):
            obj = node.child_by_field_name('object')
            return 'fetch' if obj and self.binding_node(obj) is None else None
        if node.type == 'identifier':
            scope = self.owner(self.scopes.get(node.id, 0), name)
            val = (context or {}).get((scope, name))
            if isinstance(val, dict) and '$client' in val:
                return val['$client']
            target = self.binding_node(node)
            if target is None:
                return name if name in ('axios', 'fetch') else None
            if isinstance(target, str):
                return target
            return self.client(target, context, depth + 1) if target.id != node.id else None
        if node.type == 'parenthesized_expression':
            return self.client(node.named_children[0], context, depth + 1)
        if node.type == 'member_expression' and self.text(node.child_by_field_name('property')) in ('a', 'default'):
            return self.client(node.child_by_field_name('object'), context, depth + 1)
        if node.type == 'call_expression':
            fn = node.child_by_field_name('function')
            args = node.child_by_field_name('arguments').named_children
            if len(args) == 1 and args[0].type == 'string' and self.text(fn) == 'require' and self.binding_node(fn) is None:
                library = self.string(args[0])
                return 'axios' if library == 'axios' else ('fetch' if library in ('node-fetch', 'cross-fetch') else None)
            if len(args) == 1 and args[0].type == 'number' and self.is_loader(fn):
                return self.module_client(int(self.text(args[0])))
            if len(args) == 1 and fn.type == 'member_expression' and self.text(fn.child_by_field_name('property')) == 'n' and self.is_loader(fn.child_by_field_name('object')):
                return self.client(args[0], context, depth + 1)
            if fn.type == 'member_expression' and self.text(fn.child_by_field_name('property')) == 'create':
                return self.client(fn.child_by_field_name('object'), context, depth + 1)
        return None

    def is_loader(self, node):
        if node.type != 'identifier':
            return False
        owner = self.owner(self.scopes.get(node.id, 0), self.text(node))
        function = self.function_scopes.get(owner)
        if function is None or function.id not in {n.id for n in self.modules.values()}:
            return False
        params = function.child_by_field_name('parameters')
        return bool(params and len(params.named_children) >= 3 and self.text(params.named_children[2]) == self.text(node))

    def client_options(self, node, context, depth=0):
        if not node or depth > 12:
            return {}
        if node.type == 'identifier':
            target = self.binding_node(node)
            return self.client_options(target, context, depth + 1) if target is not None and not isinstance(target, str) and target.id != node.id else {}
        if node.type == 'member_expression':
            return self.client_options(node.child_by_field_name('object'), context, depth + 1)
        if node.type == 'call_expression':
            fn = node.child_by_field_name('function')
            args = node.child_by_field_name('arguments').named_children
            if fn.type == 'member_expression' and self.text(fn.child_by_field_name('property')) == 'create' and self.client(fn.child_by_field_name('object'), context) == 'axios':
                value = self.value(args[0], context) if args else {}
                return value if isinstance(value, dict) else {'$config': value}
        return {}

    def config_value(self, config, key, default):
        if key in config:
            return config[key]
        if any(k.startswith('$') for k in config):
            return {'$expression': 'runtime_config.' + key}
        return default

    def value(self, n, context=None, depth=0):
        context = context or {}
        if n is None:
            return None
        self.budget -= 1
        if depth > 24 or self.budget < 0:
            return self.unknown(n, 'resolution limit')
        typ, text = n.type, self.text(n)
        if typ in FUNCTIONS:
            return Function(n)
        if typ == 'parenthesized_expression':
            return self.value(n.named_children[0], context, depth + 1)
        if typ == 'string':
            return self.string(n)
        if typ in ('number', 'true', 'false', 'null'):
            try:
                return json.loads(text)
            except ValueError:
                return self.unknown(n)
        if typ == 'template_string' and not any(c.type == 'template_substitution' for c in n.named_children):
            return text[1:-1]
        if typ in ('identifier', 'shorthand_property_identifier'):
            if text in ('axios', 'fetch') and self.owner(self.scopes.get(n.id, 0), text) == 0 and text not in self.bindings[0]:
                return {'$client': text}
            return self.lookup(n, text, context, depth)
        if typ == 'array':
            return [self.value(c, context, depth + 1) for c in n.named_children]
        if typ == 'object':
            result = {}
            for child in n.named_children:
                if child.type == 'pair':
                    key = child.child_by_field_name('key')
                    key = self.string(key) if key.type == 'string' else self.text(key)
                    result[key] = self.value(child.child_by_field_name('value'), context, depth + 1)
                elif child.type == 'shorthand_property_identifier':
                    result[self.text(child)] = self.value(child, context, depth + 1)
                elif child.type == 'spread_element':
                    val = self.value(child.named_children[0], context, depth + 1)
                    result.update(val if isinstance(val, dict) else {'$spread': val})
            return result
        if typ == 'member_expression':
            obj, prop = n.child_by_field_name('object'), self.text(n.child_by_field_name('property'))
            value = self.value(obj, context, depth + 1)
            if isinstance(value, dict):
                if prop in ('a', 'default') and '$client' in value:
                    return value
                if prop in value:
                    return value[prop]
            if text in ('window.fetch', 'globalThis.fetch'):
                return {'$client': 'fetch'}
            return self.unknown(n)
        if typ == 'binary_expression':
            left, right = n.child_by_field_name('left'), n.child_by_field_name('right')
            op = self.raw[left.end_byte:right.start_byte].decode().strip()
            a, b = self.value(left, context, depth + 1), self.value(right, context, depth + 1)
            if op == '+' and isinstance(a, (str, int, float)) and isinstance(b, (str, int, float)):
                def js_string(value):
                    if isinstance(value, bool):
                        return 'true' if value else 'false'
                    if isinstance(value, float) and value.is_integer():
                        return str(int(value))
                    return str(value)
                return js_string(a) + js_string(b) if isinstance(a, str) or isinstance(b, str) else a + b
            if op in ('!=', '!==', '==', '===') and not isinstance(a, (dict, list, Function)) and not isinstance(b, (dict, list, Function)):
                numbers = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (a, b))
                compatible = type(a) is type(b) or numbers
                if compatible or op in ('===', '!=='):
                    equal = compatible and a == b
                    return not equal if '!' in op else equal
            if op in ('!=', '!==', '==', '===') and (a is None or b is None):
                other = b if a is None else a
                if isinstance(other, (list, Function)) or isinstance(other, dict) and '$expression' not in other:
                    return '!' in op
            if op in ('&&', '||') and isinstance(a, bool):
                return b if (a if op == '&&' else not a) else a
            return self.unknown(n)
        if typ == 'ternary_expression':
            condition = self.value(n.child_by_field_name('condition'), context, depth + 1)
            if isinstance(condition, bool):
                return self.value(n.child_by_field_name('consequence' if condition else 'alternative'), context, depth + 1)
            return self.unknown(n)
        if typ == 'assignment_expression':
            return self.value(n.child_by_field_name('right'), context, depth + 1)
        if typ == 'sequence_expression' and n.named_children:
            return self.value(n.named_children[-1], context, depth + 1)
        if typ == 'call_expression':
            fn = n.child_by_field_name('function')
            args = n.child_by_field_name('arguments').named_children
            name = self.text(fn)
            if name == 'JSON.stringify' and args:
                return self.value(args[0], context, depth + 1)
            if len(args) == 1 and args[0].type == 'string' and name == 'require' and self.binding_node(fn) is None and self.string(args[0]) in ('axios', 'node-fetch', 'cross-fetch'):
                return {'$client': 'axios' if self.string(args[0]) == 'axios' else 'fetch'}
            if len(args) == 1 and args[0].type == 'number' and self.is_loader(fn):
                client = self.module_client(int(self.text(args[0])))
                if client:
                    return {'$client': client}
            if fn.type == 'member_expression' and self.text(fn.child_by_field_name('property')) == 'n' and self.is_loader(fn.child_by_field_name('object')) and len(args) == 1:
                val = self.value(args[0], context, depth + 1)
                if isinstance(val, dict) and '$client' in val:
                    return val
            if name == 'Object.assign':
                return self.merge(args, context, depth)
            if fn.type == 'member_expression' and self.text(fn.child_by_field_name('property')) == 'includes' and args:
                collection = self.value(fn.child_by_field_name('object'), context, depth + 1)
                item = self.value(args[0], context, depth + 1)
                if isinstance(collection, list) and all(isinstance(x, (str, int, bool)) for x in collection) and isinstance(item, (str, int, bool)):
                    return any(type(x) is type(item) and x == item for x in collection)
            target = self.value(fn, context, depth + 1)
            inferred_member = False
            if not isinstance(target, Function) and fn.type == 'member_expression':
                name = self.text(fn.child_by_field_name('property'))
                matches = self.members.get(name, [])
                if len(matches) == 1:
                    target, inferred_member = Function(matches[0]), True
            if isinstance(target, Function):
                source = self.text(target.node)
                if ('Object.keys' in source and 'Object.defineProperty' in source or
                        all(k in source for k in ('Object.getOwnPropertyDescriptors', 'Object.defineProperties', 'arguments'))):
                    return self.merge(args, context, depth)
                returns = self.returns(target.node)
                if len(returns) == 1:
                    child_context = self.call_context(target.node, args, context, depth)
                    result = self.value(returns[0], child_context, depth + 1)
                    if inferred_member and isinstance(result, dict):
                        result['$inferred_member'] = self.text(fn)
                    return result
            return self.unknown(n)
        return self.unknown(n)

    def merge(self, args, context, depth):
        out = {}
        for arg in args:
            value = self.value(arg, context, depth + 1)
            out.update(value if isinstance(value, dict) else {'$spread': value})
        return out

    def returns(self, function):
        result, stack = [], list(function.named_children)
        while stack:
            n = stack.pop()
            if n.type in FUNCTIONS:
                continue
            if n.type == 'return_statement' and n.named_children:
                result.append(n.named_children[0])
            stack.extend(n.named_children)
        return result

    def call_context(self, function, args, context, depth=0):
        out = dict(context)
        params = function.child_by_field_name('parameters')
        for i, p in enumerate(params.named_children if params else []):
            if p.type == 'identifier':
                out[(function.id, self.text(p))] = self.value(args[i], context, depth + 1) if i < len(args) else self.unknown(p)
        return out

    def clean(self, value):
        if isinstance(value, Function):
            source = self.text(value.node)
            return {'$callback': source[:1600], '$callback_truncated': len(source) > 1600,
                    'source_range': {'start_byte': value.node.start_byte, 'end_byte': value.node.end_byte}}
        if isinstance(value, dict):
            return {k: self.clean(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.clean(v) for v in value]
        return value

    def add(self, shape, node, via=None):
        if len(self.shapes) >= MAX_SHAPES:
            if 'Request-shape limit reached.' not in self.warnings:
                self.warnings.append('Request-shape limit reached.')
            return
        body_keys = set()
        stack = [(shape.get('body'), '')]
        while stack:
            value, prefix = stack.pop()
            if isinstance(value, dict):
                for key, child in value.items():
                    if str(key).startswith('$') or key == 'source_range':
                        continue
                    path = (prefix + '.' if prefix else '') + str(key)
                    body_keys.add(path)
                    stack.append((child, path))
            elif isinstance(value, list):
                stack.extend((child, prefix + '[]') for child in value)
        params = shape.get('params', {})
        query_keys = {str(k) for k in params if not str(k).startswith('$')} if isinstance(params, dict) and '$expression' not in params else set()
        import re
        query_source = params.get('$expression', '') if isinstance(params, dict) else (params if isinstance(params, str) else '')
        fragments = re.findall(r'''["']([^"']*)["']''', query_source) or [query_source]
        if isinstance(shape.get('url'), str) and '?' in shape['url']:
            fragments.append(shape['url'].split('?', 1)[1])
        for fragment in fragments:
            query_keys.update(re.findall(r'(?:^|[?&])([A-Za-z_][A-Za-z0-9_.-]*)=', fragment))
        result = self.clean({**shape, 'body_keys': sorted(body_keys), 'query_keys': sorted(query_keys),
                             'line': node.start_point.row + 1,
                             'source_range': {'start_byte': node.start_byte, 'end_byte': node.end_byte},
                             'evidence_kind': via or 'direct_call', 'review_required': bool(via)})
        if self.current_callsite:
            result['caller_range'] = self.current_callsite
        key = json.dumps(result, sort_keys=True)
        if key not in self.seen:
            self.seen.add(key)
            self.shapes.append(result)

    def network(self, node, context, via=None):
        fn = node.child_by_field_name('function')
        args = node.child_by_field_name('arguments').named_children
        if not args:
            return
        if fn.type not in ('identifier', 'member_expression'):
            return
        if fn.type == 'member_expression' and self.text(fn.child_by_field_name('property')) not in METHODS | {'request', 'fetch', 'a', 'default'}:
            return
        client = self.client(fn, context)
        method = None
        if fn.type == 'member_expression' and client != 'fetch':
            method = self.text(fn.child_by_field_name('property'))
            client = self.client(fn.child_by_field_name('object'), context)
        kind = client
        if kind and not via and self.text(fn) not in {'fetch', 'window.fetch', 'globalThis.fetch', 'axios', 'axios.request', *('axios.' + m for m in METHODS)}:
            via = 'client_alias_inference'
        handlers, parent = {}, node.parent
        for _ in range(8):
            if parent is None or parent.type not in ('member_expression', 'call_expression', 'parenthesized_expression'):
                break
            if parent.type == 'call_expression':
                callee = parent.child_by_field_name('function')
                if callee and callee.type == 'member_expression':
                    prop = self.text(callee.child_by_field_name('property'))
                    if prop in ('then', 'catch'):
                        handlers[prop] = [self.value(a, context) for a in parent.child_by_field_name('arguments').named_children]
            parent = parent.parent
        delegated = [v for v in context.values() if isinstance(v, Function)]
        if delegated:
            handlers['delegated_callbacks'] = delegated
        if kind == 'fetch' and method is None:
            cfg = self.value(args[1], context) if len(args) > 1 else {}
            cfg = cfg if isinstance(cfg, dict) else {'$config': cfg}
            self.add({'url': self.value(args[0], context), 'method': self.config_value(cfg, 'method', 'GET'),
                      'headers': self.config_value(cfg, 'headers', {}), 'body': self.config_value(cfg, 'body', None), 'transport': 'fetch',
                      'response_handlers': handlers}, node, via)
        elif kind == 'axios' and method in METHODS:
            write = method in ('post', 'put', 'patch')
            idx = 2 if write else 1
            cfg = self.value(args[idx], context) if len(args) > idx else {}
            cfg = cfg if isinstance(cfg, dict) else {'$config': cfg}
            defaults = self.client_options(fn.child_by_field_name('object'), context)
            headers = self.config_value(cfg, 'headers', defaults.get('headers', {}))
            if isinstance(headers, dict) and isinstance(defaults.get('headers'), dict):
                headers = {**defaults['headers'], **headers}
            url = self.value(args[0], context)
            if defaults.get('baseURL'):
                if isinstance(defaults['baseURL'], str) and isinstance(url, str) and not url.startswith(('http://', 'https://', '//')):
                    url = defaults['baseURL'].rstrip('/') + '/' + url.lstrip('/')
                elif not isinstance(defaults['baseURL'], str):
                    url = {'$expression': 'runtime_baseURL', '$path': url}
            self.add({'url': url, 'method': method.upper(),
                      'headers': headers, 'params': self.config_value(cfg, 'params', defaults.get('params', {})),
                      'body': self.value(args[1], context) if write and len(args) > 1 else cfg.get('data'),
                      'transport': 'axios', 'response_handlers': handlers}, node, via)
        elif kind == 'axios' and method in (None, 'request'):
            cfg = self.value(args[0], context)
            if isinstance(cfg, dict) and 'url' in cfg:
                self.add({'url': cfg['url'], 'method': cfg.get('method', 'GET'), 'headers': cfg.get('headers', {}),
                          'body': cfg.get('data'), 'params': cfg.get('params', {}), 'transport': 'axios',
                          'response_handlers': handlers}, node, via)

    def fetch_config(self, node, context, via=None):
        key = node.child_by_field_name('key')
        if self.text(key).strip('\'"') != 'fetchConfig':
            return
        cfg = self.value(node.child_by_field_name('value'), context)
        if isinstance(cfg, dict) and 'path' in cfg:
            usage = []
            for handler in (cfg.get('success'), cfg.get('error')):
                if not isinstance(handler, Function):
                    continue
                for returned in self.returns(handler.node):
                    action = self.value(returned, context)
                    tag = action.get('type') if isinstance(action, dict) else None
                    if not isinstance(tag, str):
                        continue
                    for reducer in self.reducers.get(tag, []):
                        import re
                        source = self.text(reducer)
                        fields = re.findall(r'\{\s*([A-Za-z_$][\w$]*)\s*:', source)
                        state = fields[0] if fields else None
                        usage.append({'action': tag, 'state_field_hint': state,
                                      'observed_state_reads': sorted(x for x in self.state_reads if state and x.startswith(state + '.')),
                                      'reducer_excerpt': source[:1800], 'excerpt_truncated': len(source) > 1800,
                                      'source_range': {'start_byte': reducer.start_byte, 'end_byte': reducer.end_byte}})
            self.add({'url': cfg['path'], 'method': cfg.get('method', {'$expression': 'fetchConfig.method'}),
                      'headers': cfg.get('headers', {}), 'body': cfg.get('body'),
                      'params': cfg.get('queryParams', {}), 'transport': 'fetchConfig',
                      'response_handlers': {k: cfg[k] for k in ('success', 'error') if k in cfg},
                      'response_usage': usage}, node,
                     via or 'declared_fetch_config')

    def run(self):
        candidates = {}
        for node in self.nodes:
            if node.type == 'call_expression':
                before = len(self.shapes)
                self.network(node, {})
                if len(self.shapes) > before:
                    scope = self.enclosing_function(node)
                    if scope in self.function_scopes:
                        candidates.setdefault(scope, []).append(node)
            elif node.type == 'pair' and self.text(node.child_by_field_name('key')).strip('\'"') == 'fetchConfig':
                self.fetch_config(node, {})
                scope = self.enclosing_function(node)
                if scope in self.function_scopes:
                    candidates.setdefault(scope, []).append(node)
        # Follow direct local wrappers and uniquely named exported action creators.
        for node in self.nodes:
            if node.type != 'call_expression':
                continue
            fn = node.child_by_field_name('function')
            if fn.type not in ('identifier', 'member_expression'):
                continue
            if fn.type == 'member_expression':
                name = self.text(fn.child_by_field_name('property'))
                exported = self.exports.get(name, [])
                if len(exported) != 1:
                    continue
                target, via = self.function(exported[0]), 'export_name_inference'
            else:
                target, via = self.function(fn), 'local_wrapper'
            if not isinstance(target, Function) or target.node.id not in candidates:
                continue
            context = self.call_context(target.node, node.child_by_field_name('arguments').named_children, {})
            self.current_callsite = {'start_byte': node.start_byte, 'end_byte': node.end_byte}
            for inner in candidates[target.node.id]:
                if inner.type == 'pair':
                    self.fetch_config(inner, context, via)
                else:
                    self.network(inner, context, via)
            self.current_callsite = None
        if self.tree.root_node.has_error:
            self.warnings.append('JavaScript contains syntax errors; recovered results may be incomplete.')
        if self.budget < 0:
            self.warnings.append('Symbol resolution budget reached; unresolved expressions are preserved.')
        return {'request_shapes': self.shapes, 'warnings': list(dict.fromkeys(self.warnings)),
                'parse_status': 'partial' if self.warnings else 'parsed', 'nodes_visited': len(self.nodes),
                'resolution_note': 'Static inference only; conditional and runtime values require review.'}

    def enclosing_function(self, node):
        scope = self.scopes[node.id]
        while scope not in self.function_scopes and self.parents[scope] is not None:
            scope = self.parents[scope]
        return scope


def analyze(source):
    return Analyzer(source).run()
