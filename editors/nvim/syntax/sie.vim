" Vim syntax file for Sie.
" Language: Sie
" URL:      https://github.com/sien/sielang

if exists("b:current_syntax")
  finish
endif

" declarations
syn keyword sieStorage fn let struct union enum interface
syn keyword sieStorageMod const
syn keyword sieImport import from

" control flow
syn keyword sieConditional if else case when
syn keyword sieRepeat while for foreach
syn keyword sieStatement return break continue emit defer drop
syn keyword sieException try except
syn keyword sieOperatorWord and or not as move
syn keyword sieType closure

" literals
syn keyword sieBoolean true false
syn keyword sieConstant null
syn keyword sieSelf self

" the builtin types, and the builtin declarations the prelude ships
syn keyword sieType i8 i16 i32 i64 i128 u8 u16 u32 u64 u128 f32 f64 bool char opaque raw
syn keyword sieBuiltinType Any Result Tuple Slot Scalar Iterator ConstIterator Iterable
syn keyword sieBuiltinType Clone AssignFrom Assign Destroy
syn keyword sieBuiltinType ArrayIterator ConstArrayIterator Enumerated
syn keyword sieBuiltinType ConstEnumerated EnumerateIterator
syn keyword sieBuiltinType ConstEnumerateIterator
syn keyword sieBuiltinType Add Sub Mul Div Rem
syn keyword sieBuiltinType AddAssign SubAssign MulAssign DivAssign RemAssign
syn keyword sieBuiltinType Eq Ord GetItem SetItem
syn match sieBuiltinFunc "\<\(Ok\|Error\|enumerate\)\>\ze\s*[(<]"

" a directive or decorator: '@const', '@if', '@sizeof', '@extern', and the
" rest of them, every one spelled '@name'. The ftplugin puts '@' in
" 'iskeyword', so the name never reads as a bare keyword of its own
syn match sieDirective "@\h\w*"

" the target constants the compiler defines in every program
syn keyword sieBuiltinConst TARGET_OS TARGET_ARCH
syn keyword sieBuiltinConst OS_DARWIN OS_LINUX OS_WINDOWS OS_NONE OS_UNKNOWN
syn keyword sieBuiltinConst ARCH_X86_64 ARCH_AARCH64 ARCH_RISCV64 ARCH_UNKNOWN

" a name right after 'fn' is the function being declared, its receiver
" ('S::method') highlighted as the type it acts on
syn match sieFunction "\%(\<fn\s\+\)\@<=\h\w*\%(<[^>]*>\)\=\%(::\h\w*\)\=" contains=sieReceiver
syn match sieReceiver "\h\w*\%(<[^>]*>\)\=\ze::" contained

" literals: decimal and hexadecimal integers, floats, strings, chars
syn match sieNumber "\<\d\+\>"
syn match sieNumber "\<0[xX]\x\+\>"
syn match sieFloat "\<\d\+\.\d\+\>"
syn match sieEscape contained "\\\%([abefnrtv\\'\"?]\|\o\{1,3}\|x\x\+\)"
syn region sieString start=+"+ skip=+\\\\\|\\"+ end=+"+ contains=sieEscape,@Spell
syn match sieChar "'\%(\\\%([abefnrtv\\'\"?]\|\o\{1,3}\|x\x\+\)\|[^'\\]\)'" contains=sieEscape

" operators, and the '::' and '->' that join names and signatures
syn match sieOperator "[-+*/%&|^!~<>=?:]"
syn match sieOperator "->\|::\|\*\*\|<<\|>>\|&&\|||\|[-+*/%&|^<>!=]="
syn match sieDelimiter "[();,.]"

" comments come last: where two items could match at one position Vim takes
" the one defined last, and a comment's '/' would otherwise read as the
" division operator above. '//' runs to the end of the line, '/* */' across
" lines, never nested, the compiler closing one at its first '*/'
syn keyword sieTodo contained TODO FIXME XXX NOTE
syn region sieLineComment start="//" end="$" contains=sieTodo,@Spell
syn region sieBlockComment start="/\*" end="\*/" contains=sieTodo,@Spell

hi def link sieTodo          Todo
hi def link sieLineComment   Comment
hi def link sieBlockComment  Comment
hi def link sieStorage       Structure
hi def link sieStorageMod    StorageClass
hi def link sieImport        Include
hi def link sieConditional   Conditional
hi def link sieRepeat        Repeat
hi def link sieStatement     Statement
hi def link sieException     Exception
hi def link sieOperatorWord  Operator
hi def link sieBoolean       Boolean
hi def link sieConstant      Constant
hi def link sieSelf          Identifier
hi def link sieType          Type
hi def link sieBuiltinType   Type
hi def link sieBuiltinFunc   Function
hi def link sieBuiltinConst  Constant
hi def link sieDirective     PreProc
hi def link sieFunction      Function
hi def link sieReceiver      Type
hi def link sieNumber        Number
hi def link sieFloat         Float
hi def link sieString        String
hi def link sieChar          Character
hi def link sieEscape        SpecialChar
hi def link sieOperator      Operator
hi def link sieDelimiter     Delimiter

let b:current_syntax = "sie"
