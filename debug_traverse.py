import sys
sys.path.insert(0, '.')
import streamlit as st
st.session_state.project_name = 'Test'
st.session_state.unit_select = 'mm'
st.session_state.foot_height = 100
from drawing_interface import draw_machining_view_pro_final

# Tall traverse: W_mont(550) > L_trav(262) → needs_rotation=True
L_trav, W_mont, t_tb = 262, 550, 19
tholes = [{'type':'tourillon','x':t_tb/2,'y':y,'diam_str':'d8/22'} for y in [25, 125, 225, 325, 425, 525]]
fholes = [{'type':'tourillon','x':100,'y':y,'diam_str':'d8/10'} for y in [25, 125, 225, 325]]
chants = {'Chant Avant':False,'Chant Arriere':False,'Chant Gauche':False,'Chant Droit':False}

print('needs_rotation:', W_mont > L_trav)
proj = {'project_name': 'Test', 'corps_meuble': 'C0', 'quantity': 1, 'date': '01/01/2026'}
fig = draw_machining_view_pro_final(
    'Traverse Bas (Tb)', L_trav, W_mont, t_tb, 'mm', proj, chants,
    face_holes_list=fholes,
    tranche_longue_holes_list=tholes,
    tranche_cote_holes_list=[]
)

# Panel face bounds AFTER rotation: x in [0, W_mont], y in [0, L_trav]
circles_in, circles_out = [], []
for shp in fig.layout.shapes:
    if getattr(shp, 'type', '') == 'circle':
        cx = (shp.x0 + shp.x1) / 2
        cy = (shp.y0 + shp.y1) / 2
        if 0 <= cx <= W_mont and 0 <= cy <= L_trav:
            circles_in.append((round(cx,1), round(cy,1)))
        else:
            circles_out.append((round(cx,1), round(cy,1)))

print('Circles ON panel face [0-{}] x [0-{}]:'.format(W_mont, L_trav))
for c in circles_in:
    print(' ', c)
print('Circles OUTSIDE panel (tranches):')
for c in circles_out:
    print(' ', c)
