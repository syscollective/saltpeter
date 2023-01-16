from setuptools import setup

exec(open('saltpeter/version.py').read())
setup(name='saltpeter',
      version=__version__,
      description='Distributed cron based on salt',
      url='http://github.com/syscollective/saltpeter',
      author='Marin Vintila',
      author_email='marin.vintila@syscollective.com',
      license='MIT',
      packages=['saltpeter'],
      entry_points = {
          'console_scripts': ['saltpeter=saltpeter.main:main'],
      },
      install_requires=[
          'salt',
          'crontab',
          'pyyaml',
          'tornado',
          'elasticsearch'
      ],
      zip_safe=False)
