# Frontend button style audit

## Scope and inventory

Reviewed all 217 JSX `<button>` elements in the frontend source, plus `role="button"`, button-like links, native media controls, and component CSS selectors. The source inventory covers the active landing/auth/policy, account/band administration, dashboard/workspace, calendar, gallery, member, and upload routes, as well as retained legacy song and rehearsal components. No `input[type=button|submit|reset]` controls were found.

`FileUpload` is the only `role="button"` element. It is a drag-and-drop file picker with click and Enter-key activation, styled as a drop zone. Terms and privacy remain ordinary links; inline policy and consent copy remains inline text.

## Findings and changes

| Area | Finding | Change |
| --- | --- | --- |
| Global controls | Browser button appearance and disabled states could vary by platform. | Added a low-specificity, token-based button foundation and disabled cursor/opacity. It sets no universal height or padding, leaving component geometry in control. |
| Shared form actions | Primary and secondary buttons had different sizing and behaved differently from links styled as buttons. | Standardized `.primary-btn` and `.secondary-btn` as inline-flex, 40px actions with consistent text alignment and disabled behavior. Their colors continue to use the green accent and pink danger palette. |
| Header styles | `Header.css` redefined `.primary-btn` and added a hard-coded yellow hover glow. | Removed the duplicate rule so the shared token-based action style applies on every route. |
| Landing and auth policy links | Landing login/start links were already styled, but auth policy links had layout only and inherited browser link colors. The footer also inherited auth-card spacing. | Added themed, underlined policy links with 40px targets; reset only the landing footer spacing; set landing login and CTA minimum heights. Policy text links remain semantic inline links. |
| Location search | Search used the old blue info color with white text. | Switched it to the green accent and dark on-accent text with a 40px target. |
| Comment timestamp actions | Two controls had no shared class, and the local rules duplicated basic button styling. | Applied the shared quiet action style, retaining their small contextual grouping and behavior. |
| Upload Center | Task actions had 36px targets and the close control inherited browser button treatment. | Increased task actions to 40px and gave close a compact circular 40px target. |
| Comment actions | Submit/delete actions used a blue/white combination and old gold-tinted backgrounds. | Replaced these with semantic accent/danger tokens and token-derived background tints. |

## Styles reviewed and intentionally retained

- Availability day cells, status/time tabs, calendar navigation, and selected states keep their grid and tab dimensions.
- Media playback mode, seeking, loop, playback-rate, vote, and timestamp-badge controls keep their compact specialized behavior. Native `<audio controls>` and `<video controls>` remain browser media controls.
- Workspace list rows, place-search results, gallery selection/delete controls, icon-only close/play controls, and the upload drop zone keep their component-specific layouts and accessible names.
- Existing themed action classes across account, platform-admin, workspace, dashboard, rehearsal, song, gallery, and member components remain in place; the new low-specificity baseline does not replace their authored styling.
- Link navigation remains anchors/RouterLinks, including policy links and links styled with action classes. Consent wording, policy facts, permissions, and navigation destinations were not changed.

## Verification coverage

Static inventory counted 217 source buttons and 21 without `className`; cascade review confirmed that most classless controls are intentionally styled by their surrounding component selectors. A visual fixture checks real `PolicyLinks` with shared actions, disabled state, and availability tabs. A separate preview of the real `AvailabilityCalendar` confirmed that desktop and 320px mobile layouts retain all seven date columns without horizontal overflow. Existing route tests cover anonymous landing/auth and workspace behavior; the design-token regression test now guards the shared foundation, link targets, disabled state, and scoped calendar sizing. The custom visual fixture is kept outside the production source tree.
