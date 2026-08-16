import streamlit as st
import sys
import inspect

st.write("sys.path[0]:", sys.path[0])

import database
st.write("database file:", inspect.getfile(database))
st.write("methods:", [m for m in dir(database.IngredientDB) if not m.startswith('_')])
