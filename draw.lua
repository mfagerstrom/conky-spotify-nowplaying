-- Cairo drawing for the widget, fed by nowplaying.py:
--   draw.txt      geometry (logical px) + a playback clock, rewritten 4x a second
--   lyrics.txt    timed lyric lines, rewritten when the track's lyrics change
--   bg.txt        background colour picked from the album art
--   seek-preview  fraction under the pointer while conky-mouse.py drags the seek bar
-- conky redraws every update_interval (0.05 s); between draw.txt updates the playback
-- position is advanced locally so the seek bar and lyrics move smoothly.
require 'cairo'
pcall(require, 'cairo_xlib')

local cache = os.getenv('HOME') .. '/.cache/conky-nowplaying/'
local hover_x, hover_y = -1, -1
local bg = {0.094, 0.094, 0.094}          -- current (fading) colour; starts at Spotify's #181818
local clock = {stamp = nil, pos = 0, playing = false}
local lyrics = {version = nil, synced = false, lines = {}}
local scroll = nil                        -- eased lyric scroll position (line index)

local function read(name)
    local f = io.open(cache .. name)
    if not f then return nil end
    local s = f:read('*a')
    f:close()
    return s
end

local function numbers(s)
    local t = {}
    for v in s:gmatch('%S+') do t[#t + 1] = tonumber(v) or v end
    return t
end

local function load_draw()
    local d = {}
    for line in (read('draw.txt') or ''):gmatch('[^\n]+') do
        local key, rest = line:match('^(%S+)%s+(.*)$')
        if key then d[key] = numbers(rest) end
    end
    return d
end

local function load_lyrics(version)
    if version == lyrics.version then return end
    local text = read('lyrics.txt') or ''
    local kind = text:match('^(%S+)')
    local lines = {}
    for t, l in text:gmatch('\n([%d%.]+)\t([^\n]*)') do lines[#lines + 1] = {tonumber(t), l} end
    lyrics = {version = version, synced = kind == 'synced', lines = lines}
    scroll = nil
end

function conky_mouse(event)
    if event.type == 'mouse_move' or event.type == 'mouse_enter' then
        hover_x, hover_y = event.x, event.y
    elseif event.type == 'mouse_leave' then
        hover_x, hover_y = -1, -1
    end
    return false
end

local function with_cairo(fn)
    local cs = cairo_xlib_surface_create(conky_window.display, conky_window.drawable,
                                         conky_window.visual, conky_window.width, conky_window.height)
    local cr = cairo_create(cs)
    fn(cr)
    cairo_destroy(cr)
    cairo_surface_destroy(cs)
end

local function dt()
    return (conky_info and conky_info.update_interval) or 0.05
end

-- Background ---------------------------------------------------------------------------

function conky_draw_background()
    if conky_window == nil then return end
    local r, g, b = (read('bg.txt') or ''):match('(%S+) (%S+) (%S+)')
    local target = {tonumber(r) or 0.094, tonumber(g) or 0.094, tonumber(b) or 0.094}
    local k = 1 - math.exp(-dt() * 3)                 -- ~1 s fade between tracks
    for i = 1, 3 do bg[i] = bg[i] + (target[i] - bg[i]) * k end

    local w, h = conky_window.width, conky_window.height
    local rad = math.min(24, h / 4)
    with_cairo(function(cr)
        cairo_new_sub_path(cr)
        cairo_arc(cr, w - rad, rad, rad, -math.pi / 2, 0)
        cairo_arc(cr, w - rad, h - rad, rad, 0, math.pi / 2)
        cairo_arc(cr, rad, h - rad, rad, math.pi / 2, math.pi)
        cairo_arc(cr, rad, rad, rad, math.pi, 3 * math.pi / 2)
        cairo_close_path(cr)
        cairo_set_source_rgba(cr, bg[1], bg[2], bg[3], 0.94)
        cairo_fill(cr)
    end)
end

-- Controls, seek bar, lyrics -------------------------------------------------------------

local function rounded_line(cr, x0, y0, x1, y1, width)
    cairo_set_line_width(cr, width)
    cairo_set_line_cap(cr, CAIRO_LINE_CAP_ROUND)
    cairo_move_to(cr, x0, y0)
    cairo_line_to(cr, x1, y1)
    cairo_stroke(cr)
end

local function triangle(cr, cx, cy, h, dir)
    -- right-pointing (dir = 1) or left-pointing (dir = -1) triangle with softened corners
    cairo_set_line_join(cr, CAIRO_LINE_JOIN_ROUND)
    cairo_set_line_width(cr, h * 0.25)
    cairo_move_to(cr, cx - dir * h * 0.75, cy - h)
    cairo_line_to(cr, cx + dir * h * 0.85, cy)
    cairo_line_to(cr, cx - dir * h * 0.75, cy + h)
    cairo_close_path(cr)
    cairo_fill_preserve(cr)
    cairo_stroke(cr)
end

local function hovered(x0, y0, x1, y1)
    return hover_x >= x0 and hover_x <= x1 and hover_y >= y0 and hover_y <= y1
end

local function skip_icon(cr, cx, cy, size, forward)
    -- Spotify-style previous/next: a triangle pointing at a slim bar
    local h, dir = size / 2, forward and 1 or -1
    triangle(cr, cx - dir * h * 0.2, cy, h * 0.8, dir)
    rounded_line(cr, cx + dir * h * 0.8, cy - h * 0.8, cx + dir * h * 0.8, cy + h * 0.8, size * 0.14)
end

local function draw_controls(cr, c, s)
    local prev_cx, play_cx, next_cx, cy = c[1] * s, c[2] * s, c[3] * s, c[4] * s
    local skip, play, playing = c[5] * s, c[6] * s, c[7] == 1
    for _, b in ipairs({{prev_cx, false}, {next_cx, true}}) do
        local on = hovered(b[1] - skip, cy - play / 2, b[1] + skip, cy + play / 2)
        cairo_set_source_rgba(cr, 1, 1, 1, on and 1 or 0.7)
        skip_icon(cr, b[1], cy, skip, b[2])
    end
    -- play/pause: white circle with a dark icon; grows slightly on hover
    local on = hovered(play_cx - play / 2, cy - play / 2, play_cx + play / 2, cy + play / 2)
    cairo_set_source_rgba(cr, 1, 1, 1, 1)
    cairo_arc(cr, play_cx, cy, play / 2 * (on and 1.06 or 1), 0, 2 * math.pi)
    cairo_fill(cr)
    cairo_set_source_rgba(cr, 0.07, 0.07, 0.07, 1)
    if playing then                                  -- pause: two slim rounded bars
        local gap, h = play * 0.12, play * 0.15
        rounded_line(cr, play_cx - gap, cy - h, play_cx - gap, cy + h, play * 0.1)
        rounded_line(cr, play_cx + gap, cy - h, play_cx + gap, cy + h, play * 0.1)
    else                                             -- play, nudged right to look centred
        triangle(cr, play_cx + play * 0.03, cy, play * 0.17, 1)
    end
end

local function draw_bar(cr, b, s, pos)
    local x0, x1, y, fraction = b[1] * s, b[2] * s, b[3] * s, b[4]
    if b[5] and b[5] > 0 then fraction = math.min(math.max(pos / b[5], 0), 1) end
    local preview = tonumber(read('seek-preview') or '')
    local active = preview ~= nil or hovered(x0 - 6 * s, y - 10 * s, x1 + 6 * s, y + 10 * s)
    if preview then fraction = preview end
    local fx = x0 + (x1 - x0) * fraction
    cairo_set_source_rgba(cr, 1, 1, 1, 0.3)                            -- track
    rounded_line(cr, x0, y, x1, y, 4 * s)
    if active then
        cairo_set_source_rgba(cr, 0x1d / 255, 0xb9 / 255, 0x54 / 255, 1) -- Spotify green
    else
        cairo_set_source_rgba(cr, 1, 1, 1, 1)
    end
    if fx > x0 then rounded_line(cr, x0, y, fx, y, 4 * s) end
    if active then                                                      -- knob
        cairo_set_source_rgba(cr, 1, 1, 1, 1)
        cairo_arc(cr, fx, y, 6 * s, 0, 2 * math.pi)
        cairo_fill(cr)
    end
end

local function ellipsize(cr, text, width)
    local ext = cairo_text_extents_t:create()
    cairo_text_extents(cr, text, ext)
    if ext.x_advance <= width then return text end
    while #text > 0 do
        text = text:gsub('[%z\1-\127\194-\244][\128-\191]*$', '')    -- drop one UTF-8 char
        cairo_text_extents(cr, text .. '…', ext)
        if ext.x_advance <= width then return text .. '…' end
    end
    return '…'
end

local function draw_lyrics(cr, l, s, pos)
    local x0, x1, top, row = l[1] * s, l[2] * s, l[3] * s, l[4] * s
    local lines = lyrics.lines
    if #lines == 0 then return end

    -- index of the current line, then ease the scroll towards it (~0.3 s glide)
    local idx = 1
    for i, entry in ipairs(lines) do
        if entry[1] <= pos then idx = i else break end
    end
    if scroll == nil or math.abs(scroll - idx) > 3 then scroll = idx end   -- seeks jump
    scroll = scroll + (idx - scroll) * (1 - math.exp(-dt() * 12))

    cairo_save(cr)
    cairo_rectangle(cr, x0, top, x1 - x0, row * 3)
    cairo_clip(cr)
    cairo_select_font_face(cr, 'Ubuntu Sans', CAIRO_FONT_SLANT_NORMAL, CAIRO_FONT_WEIGHT_NORMAL)
    cairo_set_font_size(cr, 11 * 96 / 72 * s)
    local centre = top + row * 1.5
    for i = math.max(1, idx - 2), math.min(#lines, idx + 2) do
        local y = centre + (i - scroll) * row
        local dist = math.min(math.abs(i - scroll), 1.5)
        local a = lyrics.synced and (1 - dist * 0.45) or 0.55             -- unsynced: all dimmed
        cairo_set_source_rgba(cr, 1, 1, 1, math.max(a, 0.2))
        cairo_move_to(cr, x0, y + row * 0.28)                             -- baseline in the row
        cairo_show_text(cr, ellipsize(cr, lines[i][2], x1 - x0))
    end
    cairo_restore(cr)
end

function conky_draw_bar()
    if conky_window == nil then return end
    local d = load_draw()
    if not (d.scale and d.bar) then return end
    local s = d.scale[1]

    -- playback clock: resync on each draw.txt update, advance locally in between
    local c = d.clock
    if c and c[1] ~= clock.stamp then
        clock = {stamp = c[1], pos = c[2], playing = c[3] == 1}
    elseif clock.playing then
        clock.pos = clock.pos + dt()
    end

    with_cairo(function(cr)
        if d.controls then draw_controls(cr, d.controls, s) end
        draw_bar(cr, d.bar, s, clock.pos)
        if d.lyrics then
            load_lyrics(d.lyrics[5])
            draw_lyrics(cr, d.lyrics, s, clock.pos)
        end
    end)
end
