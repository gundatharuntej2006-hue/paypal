import { ClientSideRowModelModule, ModuleRegistry, TextFilterModule, NumberFilterModule, CellStyleModule, RowStyleModule, HighlightChangesModule, themeQuartz } from 'ag-grid-community';

ModuleRegistry.registerModules([ClientSideRowModelModule, TextFilterModule, NumberFilterModule, CellStyleModule, RowStyleModule, HighlightChangesModule]);

export const dashboardGridTheme = themeQuartz.withParams({
  browserColorScheme: 'inherit',
  accentColor: 'var(--accent)',
  backgroundColor: 'var(--surface)',
  foregroundColor: 'var(--text)',
  borderColor: 'var(--border)',
  headerBackgroundColor: 'var(--background)',
  headerTextColor: 'var(--muted)',
  rowHoverColor: 'var(--accent-bg)',
  valueChangeValueHighlightBackgroundColor: 'var(--accent-bg)',
  fontFamily: 'inherit',
  fontSize: 14,
  rowHeight: 45,
  headerHeight: 46,
  wrapperBorder: false,
});
