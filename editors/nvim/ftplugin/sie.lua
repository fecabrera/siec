-- Buffer-local settings for Sie files: comment toggling ('gc'), four-space
-- indentation, and the '/* */' pair that 'matchit' and '%' jump over.
if vim.b.did_ftplugin then
  return
end
vim.b.did_ftplugin = true

vim.bo.commentstring = "// %s"
vim.bo.comments = "s1:/*,mb:*,ex:*/,://"

vim.bo.expandtab = true
vim.bo.shiftwidth = 4
vim.bo.softtabstop = 4
vim.bo.tabstop = 4

-- Sie mostly follows C-style indentation.  Its `name: Type;` fields do not:
-- cindent reads them as labels and aligns the following line after the colon.
-- The indent expression keeps completed fields at their body's indentation and
-- delegates every other construct to cindent.
vim.bo.cindent = true
vim.bo.cinoptions = "l1,:0,=0,g0,N-s,(0,Ws,m1,j1"
vim.bo.indentexpr = "v:lua.require'sie.indent'.get(v:lnum)"

-- '@' belongs to a directive's name, so 'gd', '*', and 'K' take '@sizeof'
-- whole rather than stopping at the sign
vim.opt_local.iskeyword:append("@-@")

vim.b.undo_ftplugin = "setlocal commentstring< comments< expandtab< "
  .. "shiftwidth< softtabstop< tabstop< cindent< cinoptions< indentexpr< "
  .. "iskeyword<"
