# component-library

Standalone iPhone 17 Pro + iOS Messages mock, assembled from existing pieces:

| Piece | Source | License |
|---|---|---|
| Device frame | Apple Design Resources, iPhone 17 Pro bezel PNG (`src/IPhone17Pro.tsx`) | Apple Design Resources license: mockups only, no redistribution, so the PNG is gitignored |
| Nav bar, bubbles, tails, typing, composer | Framework7 9 iOS theme (`framework7-react`) | MIT |
| Icons | `framework7-icons` | MIT |
| Status bar, keyboard | `src/vendor/ios-chrome.tsx`, from zoewu-creator/texting-ui-templates | MIT |

```
pnpm install
pnpm fetch-bezel   # downloads Apple's bezel DMG (265 MB) into public/bezels/
pnpm dev           # http://localhost:5174, add ?keyboard for the keyboard-up state
```
