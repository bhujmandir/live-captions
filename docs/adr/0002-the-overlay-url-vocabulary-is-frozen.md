# The overlay URL's vocabulary is frozen, even where it is wrong

The caption overlay's settings ride in the query string of the URL stored inside the vMix
browser input — `?block=`, `?panel=`, `?overlay=1`. That vocabulary is now fixed: we do not
rename a query parameter to fix a naming problem, we fix the naming everywhere else and leave
the URL alone.

The reason is that the URL is not ours. It lives in a vMix preset on the mandir PC, and the only
way to change it is for a person to paste a new one into the input's own settings and re-save
the preset. `SetBrowserURL` is not a vMix 26 function, and `BrowserNavigate` changes the live
page but not the stored URL, so it reverts on the next vMix restart or preset reload (#34). A
rename would therefore not fail loudly — it would work in the browser, work in every test, and
then silently revert the hall overlay to defaults the next time somebody restarted vMix, most
likely in the twenty minutes before a katha.

## The one this was decided over

`block` means two different things: to Manish it is the translucent slab behind the words —
*"i need 25% capacity block background"* — and to the tool it is the reserved rectangle that
text flows around. Both are load-bearing, both appear in the overlay URL, and one of them is
badly named. `CONTEXT.md` resolves it in language: the slab is **the panel**, the rectangle is
**the reserved block**, and neither is ever just "block". The parameter names stay as they are.

## Consequences

- The glossary and the wire format are allowed to disagree, and this is the first place they
  deliberately do. Anywhere else, a mismatch between a name and its meaning is a bug.
- New settings should be named for what they mean, because a *new* parameter has no preset
  holding it hostage. The freeze applies to what is already deployed, not to what is added.
- If a rename ever does become worth it, it costs a person standing at the mandir PC with the
  preset open, and it has to be scheduled with a clear hall — not shipped.
