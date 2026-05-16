; tree-sitter queries for Python: extract def / call / inherit / import edges.
; Captures consumed by `reposage.indexer.symbol_graph`.

; --- definitions ---
(function_definition
  name: (identifier) @def.function)

(class_definition
  name: (identifier) @def.class)

; --- calls ---
(call
  function: [
    (identifier) @call.callee
    (attribute attribute: (identifier) @call.callee)
  ])

; --- inheritance ---
(class_definition
  superclasses: (argument_list (identifier) @inherit.parent))

; --- imports ---
(import_statement
  name: (dotted_name) @import.module)

(import_from_statement
  module_name: (dotted_name) @import.module)
