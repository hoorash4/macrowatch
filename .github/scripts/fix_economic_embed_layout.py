from pathlib import Path

css_path = Path('assets/css/economic-charts.css')
css = css_path.read_text()
old = 'body.dashboard-economic-view #app-shell{max-width:1600px}'
new = 'body.dashboard-economic-view #app-shell{--dashboard-content-max-width:1540px}'
if old not in css:
    raise SystemExit('economic width rule not found')
css = css.replace(old, new, 1)
if '#app-shell .mobile-bottom-nav{grid-template-columns:repeat(6,minmax(0,1fr))}' not in css:
    css += '\n@media(max-width:1023px){#app-shell .mobile-bottom-nav{grid-template-columns:repeat(6,minmax(0,1fr))}}\n'
css_path.write_text(css)

ui_path = Path('tests/test_economic_account_ui.js')
ui = ui_path.read_text()
ui = ui.replace(
    "assert.match(css, /body\\.dashboard-economic-view #app-shell\\{max-width:1600px\\}/);",
    "assert.match(css, /body\\.dashboard-economic-view #app-shell\\{--dashboard-content-max-width:1540px\\}/);\nassert.match(css, /#app-shell \\.mobile-bottom-nav\\{grid-template-columns:repeat\\(6,minmax\\(0,1fr\\)\\)\\}/);",
)
ui_path.write_text(ui)
print('economic embed layout contracts fixed')
