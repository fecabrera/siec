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

-- C-style indenting fits: braces, parentheses, and 'case' arms all follow
-- the same shapes
vim.bo.cindent = true
vim.bo.cinoptions = "l1,:0,=0,g0,N-s,(0,Ws,m1,j1"

-- '@' belongs to a directive's name, so 'gd', '*', and 'K' take '@sizeof'
-- whole rather than stopping at the sign
vim.opt_local.iskeyword:append("@-@")

vim.b.undo_ftplugin = "setlocal commentstring< comments< expandtab< "
  .. "shiftwidth< softtabstop< tabstop< cindent< cinoptions< iskeyword<"
