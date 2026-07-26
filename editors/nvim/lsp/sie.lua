-- The sie-lsp language server, in the form Neovim 0.11+ reads from a
-- plugin's 'lsp/' directory: 'vim.lsp.enable("sie")' starts it.
--
-- It gives diagnostics as you type, hover ('K'), go-to-definition ('grd'
-- or 'gd'), and the document outline. The server comes with the compiler:
-- 'pip install -e ".[lsp]"'.
--
-- The include path comes from the project's package.toml ([package]
-- include); extra directories (the compiler's -I) go in settings, see the
-- README.
return {
  cmd = { "sie-lsp" },
  filetypes = { "sie" },

  -- a project is whatever holds the package manifest; a lone file falls
  -- back to its own directory
  root_markers = { "package.toml", ".git" },

  init_options = { includePaths = {} },
}
