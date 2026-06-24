
from setuptools import setup

with open('README.md', 'r', encoding='utf-8') as file:
    long_description = file.read()

setup(name='openleadr',
      version='0.5.34',
      description='Python3 library for building OpenADR Clients (VENs) and Servers (VTNs)',
      long_description=long_description,
      long_description_content_type='text/markdown',
      url='https://openleadr.org',
      project_urls={'GitHub': 'https://github.com/openleadr/openleadr-python',
                    'Documentation': 'https://openleadr.org/docs'},
      packages=['openleadr', 'openleadr.service'],
      python_requires='>=3.7.0',
      include_package_data=True,
      install_requires=['xmltodict==0.13.0', 'aiohttp>=3.8.3,<4.0.0', 'apscheduler>=3.10.0,<4.0.0', 'jinja2>=3.1.2,<4.0.0', 'signxml==3.2.1'],
      entry_points={'console_scripts': ['fingerprint = openleadr.fingerprint:show_fingerprint']})
