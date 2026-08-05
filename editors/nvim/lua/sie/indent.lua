local M = {}

-- A final parameter can look like a field (`name: Type);`).  Walk back to the
-- nearest statement boundary to see whether an earlier opening parenthesis is
-- still active at the start of this line.
local function continues_parenthesized_expression(line_number)
  local nested_closers = 0

  for number = line_number - 1, 1, -1 do
    local line = vim.fn.getline(number):gsub("//.*$", "")

    for index = #line, 1, -1 do
      local character = line:sub(index, index)
      if character == ")" then
        nested_closers = nested_closers + 1
      elseif character == "(" then
        if nested_closers == 0 then
          return true
        end
        nested_closers = nested_closers - 1
      else
        local boundary = character == ";" or character == "{"
          or character == "}"
        if nested_closers == 0 and boundary then
          return false
        end
      end
    end
  end

  return false
end

local function is_field(line_number)
  local line = vim.fn.getline(line_number)
  local matches = line:match("^%s*[%a_][%w_]*%s*:%s*.-;%s*$")
    or line:match("^%s*[%a_][%w_]*%s*:%s*.-;%s*//")
  return matches and not continues_parenthesized_expression(line_number)
end

function M.get(line_number)
  local line = vim.fn.getline(line_number)
  local previous = vim.fn.prevnonblank(line_number - 1)

  -- Keep a field where insert-mode indentation put it.  This also prevents
  -- typing its colon from making cindent treat the declaration as a label.
  if is_field(line_number) then
    local current = vim.fn.indent(line_number)
    if current > 0 then
      return current
    end

    if previous > 0 then
      local previous_line = vim.fn.getline(previous)
      if previous_line:match("{%s*$") then
        return vim.fn.indent(previous) + vim.fn.shiftwidth()
      end
      if is_field(previous) then
        return vim.fn.indent(previous)
      end
    end
  end

  if previous > 0 and is_field(previous) then
    if not line:match("^%s*}") then
      return vim.fn.indent(previous)
    end
  end

  return vim.fn.cindent(line_number)
end

return M
