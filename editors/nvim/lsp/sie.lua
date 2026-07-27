-- The sie-lsp language server, in the form Neovim 0.11+ reads from a
-- plugin's 'lsp/' directory: 'vim.lsp.enable("sie")' starts it.
--
-- It gives diagnostics as you type, hover ('K'), go-to-definition ('grd'
-- or 'gd'), and the document outline. The server comes with the compiler:
-- 'pip install -e ".[lsp]"'.
--
-- A project is whatever holds the nearest package.toml, so opening a
-- package inside a workspace roots the server at that package: its own
-- sources and its installed dependencies' are what its imports resolve
-- through. Extra directories (the compiler's -I) go in settings, see the
-- README.
return {
  cmd = { "sie-lsp" },
  filetypes = { "sie" },

  -- a project is whatever holds the package manifest; a lone file falls
  -- back to its own directory
  root_markers = { "package.toml", ".git" },

  init_options = { includePaths = {} },
}
