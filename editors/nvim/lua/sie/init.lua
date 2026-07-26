-- Registers the Sie parser with nvim-treesitter so ':TSInstall sie' can
-- build it from this repository.
--
--   require("sie").setup({ path = "/path/to/sielang/editors/tree-sitter-sie" })
--
-- Then ':TSInstall sie'. Without nvim-treesitter, build the parser by hand
-- (see the README) and drop the result in 'parser/sie.so' on the
-- runtimepath: the queries here are found either way.
local M = {}

function M.setup(opts)
  opts = opts or {}

  local ok, parsers = pcall(require, "nvim-treesitter.parsers")
  if not ok then
    return
  end

  local list = parsers.get_parser_configs and parsers.get_parser_configs() or parsers
  list.sie = {
    install_info = {
      url = opts.path or vim.fn.expand("<sfile>:p:h:h:h") .. "/../tree-sitter-sie",
      files = { "src/parser.c" },
      branch = opts.branch or "main",
      generate_requires_npm = false,
      requires_generate_from_grammar = false,
    },
    filetype = "sie",
  }
end

return M
